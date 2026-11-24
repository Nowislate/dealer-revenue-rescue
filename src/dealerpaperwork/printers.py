"""Printer access, behind a small provider interface (Stages 2 + 4).

Two things live behind this interface:

* enumerate the printers Windows knows about, so the setup wizard can
  offer a list to pick from (Stage 2), and
* actually print a rendered document: ``print_document`` (Stage 4).
  The Windows implementation draws every page through the printer's
  own Windows driver (GDI and GDI+), so any printer Windows can print
  to works, and ``send_test_print`` is wired through it. The fake provider used
  in tests/development records every job in full -- printer, tray,
  paper, copies, calibration offsets, payload -- and can be told to
  fail specific document ids so the pipeline's failure/resume logic
  can be exercised headless.

Payloads are text (checklists, test pages) or the PDF bytes the
overlay renderer writes; ``page_drawing`` turns either into pages.

Honest limitation: the job uses the driver's default paper and tray.
The pipeline resolves and records the tray/paper from the setup
mapping for every job, but driving a specific tray through DEVMODE on
each printer model is per-driver behaviour, validated on the store's
real printers.

Standard library only. The Windows paths talk to ``winspool.drv``,
``gdi32.dll`` and ``gdiplus.dll`` through ctypes. They are guarded so this module imports cleanly on
non-Windows machines -- the development sandbox is Linux, so those
paths are written carefully for Windows but never executed (or
imported into symbols) off Windows.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class TestPrintResult:
    """Outcome of a test-print action."""

    ok: bool
    printer_name: str
    message: str = ""


@dataclass
class PrintJobResult:
    """Outcome of one ``print_document`` call."""

    ok: bool
    printer_name: str
    message: str = ""
    job_id: Optional[int] = None  # spooler job id (Windows), else None


@dataclass
class PrintJob:
    """One document-print job, as submitted to a provider.

    The fake provider records these verbatim; the pipeline keeps the
    same details on its checklist items.
    """

    printer_name: str
    payload: object  # str (text renders) or bytes (Stage 5 PDF overlays)
    doc_id: str = ""
    title: str = ""
    tray: Optional[str] = None
    paper: Optional[str] = None
    copies: int = 1
    offsets_mm: dict = field(default_factory=dict)


class PrinterProvider(ABC):
    """Interface the app depends on. Nothing above this layer should
    talk to the OS printing stack directly."""

    @abstractmethod
    def list_printers(self) -> list[str]:
        """Names of printers available on this machine, in OS order."""
        raise NotImplementedError

    @abstractmethod
    def send_test_print(self, printer_name: str) -> TestPrintResult:
        """Run the test-print action for ``printer_name``."""
        raise NotImplementedError

    @abstractmethod
    def print_document(
        self,
        printer_name: str,
        payload,
        *,
        doc_id: str = "",
        title: str = "",
        tray: Optional[str] = None,
        paper: Optional[str] = None,
        copies: int = 1,
        offsets_mm: Optional[dict] = None,
    ) -> PrintJobResult:
        """Print one rendered document.

        ``payload`` is the rendered document: text (Stage 4's
        renderer) or PDF bytes (Stage 5's overlay renderer for dealer
        documents), behind this same interface. ``tray``/``paper``
        come from the Stage 2 setup mapping, ``copies`` from the
        copy-set presets, and ``offsets_mm`` carries the
        per-printer/per-document alignment calibration the renderer
        already applied to the payload -- providers record/pass it
        with the job for traceability.
        """
        raise NotImplementedError


class FakePrinterProvider(PrinterProvider):
    """In-memory provider for tests and development on any OS.

    Configurable printer names; records every test print and every
    document job in full. ``fail_on`` is a set of doc_ids whose
    document jobs should fail (mutable: clear an id to "fix" the
    printer mid-test and exercise resume).
    """

    DEFAULT_PRINTERS = ("FakePrinter - Office (Letter)", "FakePrinter - F&I (Letter)")

    def __init__(
        self,
        printer_names: Optional[list[str]] = None,
        *,
        fail_on: Optional[Iterable[str]] = None,
    ) -> None:
        self._printer_names = list(
            printer_names if printer_names is not None else self.DEFAULT_PRINTERS
        )
        self.test_prints: list[str] = []
        self.jobs: list[PrintJob] = []
        self.fail_on: set[str] = set(fail_on or ())

    def list_printers(self) -> list[str]:
        return list(self._printer_names)

    def send_test_print(self, printer_name: str) -> TestPrintResult:
        if printer_name not in self._printer_names:
            return TestPrintResult(
                ok=False,
                printer_name=printer_name,
                message=f"Unknown printer {printer_name!r} (fake provider).",
            )
        self.test_prints.append(printer_name)
        return TestPrintResult(
            ok=True,
            printer_name=printer_name,
            message="Test print recorded by the fake provider (no paper used).",
        )

    def print_document(
        self,
        printer_name: str,
        payload,
        *,
        doc_id: str = "",
        title: str = "",
        tray: Optional[str] = None,
        paper: Optional[str] = None,
        copies: int = 1,
        offsets_mm: Optional[dict] = None,
    ) -> PrintJobResult:
        if printer_name not in self._printer_names:
            return PrintJobResult(
                ok=False,
                printer_name=printer_name,
                message=f"Unknown printer {printer_name!r} (fake provider).",
            )
        job = PrintJob(
            printer_name=printer_name,
            payload=payload,
            doc_id=doc_id,
            title=title,
            tray=tray,
            paper=paper,
            copies=copies,
            offsets_mm=dict(offsets_mm or {}),
        )
        self.jobs.append(job)
        if doc_id in self.fail_on:
            return PrintJobResult(
                ok=False,
                printer_name=printer_name,
                message=(
                    f"Simulated print failure for document {doc_id!r} "
                    "(fake provider fail_on)."
                ),
            )
        return PrintJobResult(
            ok=True,
            printer_name=printer_name,
            message="Document job recorded by the fake provider (no paper used).",
            job_id=len(self.jobs),
        )

    def jobs_for(self, doc_id: str) -> list[PrintJob]:
        """All recorded jobs for one document id, in submission order."""
        return [job for job in self.jobs if job.doc_id == doc_id]


# ---------------------------------------------------------------------------
# Windows: enumerate printers through the spooler (winspool.drv).
#
# EnumPrintersW with PRINTER_INFO_4 is the simplest reliable shape for
# name enumeration: each record is {LPWSTR pPrinterName; LPWSTR
# pServerName; DWORD Attributes}. We ask for PRINTER_ENUM_LOCAL (locally
# installed printers) | PRINTER_ENUM_CONNECTIONS (network connections),
# which covers the shared network printer the setup wizard looks for.
# ---------------------------------------------------------------------------

_PRINTER_ENUM_LOCAL = 0x00000002
_PRINTER_ENUM_CONNECTIONS = 0x00000004


class WindowsPrinterProvider(PrinterProvider):
    """Printer provider backed by the Windows print spooler.

    Only constructed on Windows (see ``get_printer_provider``); the
    ctypes loading happens lazily inside the methods so importing this
    module anywhere else is harmless.
    """

    def list_printers(self) -> list[str]:
        import ctypes
        from ctypes import wintypes

        class _PRINTER_INFO_4(ctypes.Structure):
            _fields_ = [
                ("pPrinterName", wintypes.LPWSTR),
                ("pServerName", wintypes.LPWSTR),
                ("Attributes", wintypes.DWORD),
            ]

        winspool = ctypes.WinDLL("winspool.drv")
        enum_printers = winspool.EnumPrintersW
        enum_printers.argtypes = [
            wintypes.DWORD,  # Flags
            wintypes.LPWSTR,  # Name (NULL for local enumeration)
            wintypes.DWORD,  # Level (4)
            ctypes.POINTER(ctypes.c_byte),  # pPrinterEnum buffer
            wintypes.DWORD,  # cbBuf
            ctypes.POINTER(wintypes.DWORD),  # pcbNeeded
            ctypes.POINTER(wintypes.DWORD),  # pcReturned
        ]
        enum_printers.restype = wintypes.BOOL

        flags = _PRINTER_ENUM_LOCAL | _PRINTER_ENUM_CONNECTIONS
        needed = wintypes.DWORD(0)
        returned = wintypes.DWORD(0)
        # First call with no buffer: fails and reports the required size.
        enum_printers(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
        if needed.value == 0:
            return []
        buffer = (ctypes.c_byte * needed.value)()
        ok = enum_printers(
            flags,
            None,
            4,
            buffer,
            needed.value,
            ctypes.byref(needed),
            ctypes.byref(returned),
        )
        if not ok:
            raise OSError("EnumPrintersW failed while listing printers.")
        records = (_PRINTER_INFO_4 * returned.value).from_address(
            ctypes.addressof(buffer)
        )
        names: list[str] = []
        for record in records:
            if record.pPrinterName:
                names.append(record.pPrinterName)
        return names

    # -- Printing through the printer's Windows driver --------------------
    #
    # Every job is DRAWN through the printer's own Windows driver, the
    # way Word or a browser prints: CreateDC on the printer, StartDoc,
    # then per page the form image (GDI+, which reads JPEG natively)
    # and each value (GDI TextOut), EndPage, EndDoc. The driver turns
    # that into the printer's own language (PCL, PostScript, its
    # maker's), so any printer Windows can print to works. The printer
    # never has to read PDF itself. ``page_drawing`` turns the payload
    # into pages and is tested on every machine; this method only draws
    # them. Tray/paper are still recorded with the job; the driver's
    # default paper and tray are used (set them in the printer's
    # Windows settings).

    def __init__(self, output_file: Optional[str] = None) -> None:
        # Set only by the Windows CI proof: a driver such as "Microsoft
        # Print to PDF" then writes the job to this file instead of
        # asking where to save it. Never set in the app.
        self.output_file = output_file

    def print_document(
        self,
        printer_name: str,
        payload,
        *,
        doc_id: str = "",
        title: str = "",
        tray: Optional[str] = None,
        paper: Optional[str] = None,
        copies: int = 1,
        offsets_mm: Optional[dict] = None,
    ) -> PrintJobResult:
        from . import page_drawing

        try:
            pages = page_drawing.pages_from_payload(payload)
        except page_drawing.PageDrawingError as exc:
            return PrintJobResult(ok=False, printer_name=printer_name,
                                  message=f"Not printed: {exc}")
        doc_name = title or doc_id or "Dealer Revenue Rescue document"
        try:
            job_id = _draw_job(printer_name, doc_name, pages,
                               max(1, int(copies)), self.output_file)
        except OSError as exc:  # pragma: no cover - Windows only
            return PrintJobResult(ok=False, printer_name=printer_name,
                                  message=str(exc))
        return PrintJobResult(
            ok=True,
            printer_name=printer_name,
            message=(
                f"Sent {len(pages)} page(s) x {max(1, int(copies))} to "
                "the printer's Windows driver."
            ),
            job_id=job_id,
        )

    def send_test_print(self, printer_name: str) -> TestPrintResult:
        # A real one-page job through the printer's Windows driver.
        payload = (
            "Dealer Revenue Rescue - test page\r\n"
            f"Printer: {printer_name}\r\n"
            "If this page printed in full, printing works.\r\n"
        )
        result = self.print_document(
            printer_name, payload, doc_id="test_page", title="Test page"
        )
        return TestPrintResult(
            ok=result.ok, printer_name=printer_name, message=result.message
        )


# ---------------------------------------------------------------------------
# Drawing a job through a Windows printer driver (GDI + GDI+), via ctypes.
# Windows only; nothing below runs or loads a DLL until a job prints.
# ---------------------------------------------------------------------------

_LOGPIXELSX, _LOGPIXELSY = 88, 90
_PHYSICALOFFSETX, _PHYSICALOFFSETY = 112, 113
_TRANSPARENT = 1
_TA_BASELINE = 24
_FW_NORMAL, _FW_BOLD = 400, 700
_FACES = {"regular": "Arial", "bold": "Arial", "mono": "Courier New"}


def _rgb(rgb) -> int:
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb)
    return r | (g << 8) | (b << 16)


def _draw_job(printer_name, doc_name, pages, copies, output_file):  # pragma: no cover - Windows only
    """Draw ``pages`` ``copies`` times as one job; returns the job id."""
    import ctypes
    from ctypes import wintypes

    from . import page_drawing

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    class DOCINFOW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_int), ("lpszDocName", wintypes.LPCWSTR),
                    ("lpszOutput", wintypes.LPCWSTR), ("lpszDatatype", wintypes.LPCWSTR),
                    ("fwType", wintypes.DWORD)]

    gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    gdi32.CreateDCW.restype = wintypes.HDC
    gdi32.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(DOCINFOW)]
    gdi32.StartDocW.restype = ctypes.c_int
    for name in ("StartPage", "EndPage", "EndDoc", "AbortDoc", "DeleteDC"):
        getattr(gdi32, name).argtypes = [wintypes.HDC]
        getattr(gdi32, name).restype = ctypes.c_int
    gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetTextAlign.argtypes = [wintypes.HDC, wintypes.UINT]
    gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
    gdi32.TextOutW.restype = wintypes.BOOL

    hdc = gdi32.CreateDCW("WINSPOOL", printer_name, None, None)
    if not hdc:
        raise OSError(f"Windows could not open the printer {printer_name!r} "
                      f"(error {ctypes.get_last_error()}).")
    images = _GdiPlus() if any(p.jpeg for p in pages) else None
    try:
        geometry = page_drawing.DeviceGeometry(
            dpi_x=gdi32.GetDeviceCaps(hdc, _LOGPIXELSX),
            dpi_y=gdi32.GetDeviceCaps(hdc, _LOGPIXELSY),
            offset_x=gdi32.GetDeviceCaps(hdc, _PHYSICALOFFSETX),
            offset_y=gdi32.GetDeviceCaps(hdc, _PHYSICALOFFSETY),
        )
        info = DOCINFOW(ctypes.sizeof(DOCINFOW), doc_name, output_file, None, 0)
        job_id = gdi32.StartDocW(hdc, ctypes.byref(info))
        if job_id <= 0:
            raise OSError(f"The printer {printer_name!r} refused the job "
                          f"(error {ctypes.get_last_error()}).")
        try:
            fonts: dict = {}
            for _ in range(copies):
                for page in pages:
                    if gdi32.StartPage(hdc) <= 0:
                        raise OSError(f"The printer {printer_name!r} refused a page "
                                      f"(error {ctypes.get_last_error()}).")
                    if page.jpeg:
                        images.draw(hdc, page.jpeg, page_drawing.page_rect(geometry))
                    gdi32.SetBkMode(hdc, _TRANSPARENT)
                    gdi32.SetTextAlign(hdc, _TA_BASELINE)
                    for run in page.runs:
                        key = (run.font, run.size_pt)
                        if key not in fonts:
                            fonts[key] = gdi32.CreateFontW(
                                page_drawing.font_height(geometry, run.size_pt), 0, 0, 0,
                                _FW_BOLD if run.font == "bold" else _FW_NORMAL,
                                0, 0, 0, 1, 0, 0, 0, 0, _FACES.get(run.font, "Arial"))
                        old = gdi32.SelectObject(hdc, fonts[key])
                        gdi32.SetTextColor(hdc, _rgb(run.rgb))
                        x, y = page_drawing.to_device(geometry, run.x_pt, run.y_pt)
                        gdi32.TextOutW(hdc, x, y, run.text, len(run.text))
                        gdi32.SelectObject(hdc, old)
                    if gdi32.EndPage(hdc) <= 0:
                        raise OSError(f"The printer {printer_name!r} failed a page "
                                      f"(error {ctypes.get_last_error()}).")
            for font in fonts.values():
                gdi32.DeleteObject(font)
        except BaseException:
            gdi32.AbortDoc(hdc)
            raise
        if gdi32.EndDoc(hdc) <= 0:
            raise OSError(f"The printer {printer_name!r} did not finish the job "
                          f"(error {ctypes.get_last_error()}).")
        return int(job_id)
    finally:
        if images is not None:
            images.close()
        gdi32.DeleteDC(hdc)


class _GdiPlus:  # pragma: no cover - Windows only
    """GDI+ (part of Windows) to draw a JPEG onto a printer page."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class StartupInput(ctypes.Structure):
            _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                        ("SuppressBackgroundThread", wintypes.BOOL),
                        ("SuppressExternalCodecs", wintypes.BOOL)]

        self.ctypes = ctypes
        self.lib = lib = ctypes.WinDLL("gdiplus")
        self.shlwapi = ctypes.WinDLL("shlwapi")
        self.shlwapi.SHCreateMemStream.argtypes = [ctypes.c_char_p, wintypes.UINT]
        self.shlwapi.SHCreateMemStream.restype = ctypes.c_void_p
        lib.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(StartupInput), ctypes.c_void_p]
        lib.GdiplusShutdown.argtypes = [ctypes.c_size_t]
        lib.GdipCreateBitmapFromStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        lib.GdipCreateFromHDC.argtypes = [wintypes.HDC, ctypes.POINTER(ctypes.c_void_p)]
        lib.GdipSetPageUnit.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.GdipSetInterpolationMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.GdipDrawImageRectI.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4
        lib.GdipDeleteGraphics.argtypes = [ctypes.c_void_p]
        lib.GdipDisposeImage.argtypes = [ctypes.c_void_p]
        self.token = ctypes.c_size_t(0)
        status = lib.GdiplusStartup(ctypes.byref(self.token), ctypes.byref(StartupInput(1, None, False, False)), None)
        if status != 0:
            raise OSError(f"Windows could not start GDI+ (status {status}).")

    def _check(self, status, what):
        if status != 0:
            raise OSError(f"Windows could not {what} (GDI+ status {status}).")

    def draw(self, hdc, jpeg: bytes, rect) -> None:
        ctypes, lib = self.ctypes, self.lib
        stream = self.shlwapi.SHCreateMemStream(jpeg, len(jpeg))
        if not stream:
            raise OSError("Windows could not hold the form image in memory.")
        bitmap, graphics = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            self._check(lib.GdipCreateBitmapFromStream(stream, ctypes.byref(bitmap)), "read the form image")
            self._check(lib.GdipCreateFromHDC(hdc, ctypes.byref(graphics)), "draw on the printer page")
            lib.GdipSetPageUnit(graphics, 2)  # UnitPixel: device pixels, like GDI
            lib.GdipSetInterpolationMode(graphics, 7)  # high-quality bicubic
            self._check(lib.GdipDrawImageRectI(graphics, bitmap, *rect), "draw the form image")
        finally:
            if graphics:
                lib.GdipDeleteGraphics(graphics)
            if bitmap:
                lib.GdipDisposeImage(bitmap)
            vtable = ctypes.cast(stream, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(stream)  # IStream::Release

    def close(self) -> None:
        self.lib.GdiplusShutdown(self.token)


def get_printer_provider() -> PrinterProvider:
    """The provider for this machine: the Windows spooler provider on
    Windows, the fake provider everywhere else (tests/development)."""
    if sys.platform.startswith("win"):
        return WindowsPrinterProvider()
    return FakePrinterProvider()
