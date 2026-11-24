"""Printer access, behind a small provider interface (Stages 2 + 4).

Two things live behind this interface:

* enumerate the printers Windows knows about, so the setup wizard can
  offer a list to pick from (Stage 2), and
* actually print a rendered document: ``print_document`` (Stage 4).
  The Windows implementation sends the payload to the spooler as a
  RAW job (OpenPrinter / StartDocPrinter / WritePrinter), and
  ``send_test_print`` is now wired through it. The fake provider used
  in tests/development records every job in full -- printer, tray,
  paper, copies, calibration offsets, payload -- and can be told to
  fail specific document ids so the pipeline's failure/resume logic
  can be exercised headless.

Payloads are text in Stages 2-4; Stage 5's dealer-document overlay
renderer produces PDF *bytes*, and providers accept either --
``PrintJob.payload`` is ``str | bytes`` and the Windows path writes
bytes through unchanged.

Honest limitation, recorded for the handover: a RAW spooler job
carries the payload bytes, but tray and paper selection on Windows is
normally driven by the printer driver's DEVMODE, not by the RAW job
itself. The pipeline resolves and records the tray/paper from the
Stage 2 setup mapping for every job, but driving a specific tray
through DEVMODE on each printer model is physical, per-driver
behaviour -- it is validated on the store's real printers at the
dealer site, like the Stage 2 enumeration path.

Standard library only. The Windows paths talk to ``winspool.drv``
through ctypes. They are guarded so this module imports cleanly on
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

    # -- RAW spooler printing (Stage 4) ------------------------------------
    #
    # The job is sent as datatype "RAW": the payload bytes go to the
    # printer exactly as given (StartDocPrinterW level 1 with a
    # DOC_INFO_1, StartPagePrinter, WritePrinter, EndPagePrinter,
    # EndDocPrinter, ClosePrinter). This is the classic winspool path
    # for pre-rendered content. Tray/paper are accepted and recorded,
    # but -- as the module docstring says -- true tray selection is a
    # DEVMODE/driver matter and is confirmed physically at the dealer
    # site; the payload itself is what this path guarantees.

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
        import ctypes
        from ctypes import wintypes

        class _DOC_INFO_1(ctypes.Structure):
            _fields_ = [
                ("pDocName", wintypes.LPWSTR),
                ("pOutputFile", wintypes.LPWSTR),
                ("pDatatype", wintypes.LPWSTR),
            ]

        try:
            winspool = ctypes.WinDLL("winspool.drv")
        except OSError as exc:  # pragma: no cover - Windows only
            return PrintJobResult(
                ok=False,
                printer_name=printer_name,
                message=f"Could not load winspool.drv: {exc}",
            )

        winspool.OpenPrinterW.argtypes = [
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.c_void_p,
        ]
        winspool.OpenPrinterW.restype = wintypes.BOOL
        winspool.StartDocPrinterW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(_DOC_INFO_1),
        ]
        winspool.StartDocPrinterW.restype = wintypes.DWORD
        winspool.StartPagePrinter.argtypes = [wintypes.HANDLE]
        winspool.StartPagePrinter.restype = wintypes.BOOL
        winspool.WritePrinter.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        winspool.WritePrinter.restype = wintypes.BOOL
        winspool.EndPagePrinter.argtypes = [wintypes.HANDLE]
        winspool.EndPagePrinter.restype = wintypes.BOOL
        winspool.EndDocPrinter.argtypes = [wintypes.HANDLE]
        winspool.EndDocPrinter.restype = wintypes.BOOL
        winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
        winspool.ClosePrinter.restype = wintypes.BOOL

        data = payload if isinstance(payload, bytes) else payload.encode("utf-8", errors="replace")
        doc_name = title or doc_id or "Dealer Revenue Rescue document"
        last_job_id: Optional[int] = None
        for _ in range(max(1, int(copies))):
            handle = wintypes.HANDLE()
            if not winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
                return PrintJobResult(
                    ok=False,
                    printer_name=printer_name,
                    message=(
                        f"OpenPrinter failed for {printer_name!r} "
                        f"(Windows error {ctypes.get_last_error()})."
                    ),
                )
            try:
                info = _DOC_INFO_1(doc_name, None, "RAW")
                job_id = winspool.StartDocPrinterW(handle, 1, ctypes.byref(info))
                if job_id == 0:
                    return PrintJobResult(
                        ok=False,
                        printer_name=printer_name,
                        message=(
                            "StartDocPrinter failed "
                            f"(Windows error {ctypes.get_last_error()})."
                        ),
                    )
                last_job_id = int(job_id)
                try:
                    if not winspool.StartPagePrinter(handle):
                        return PrintJobResult(
                            ok=False,
                            printer_name=printer_name,
                            message=(
                                "StartPagePrinter failed "
                                f"(Windows error {ctypes.get_last_error()})."
                            ),
                        )
                    buffer = (ctypes.c_char * len(data)).from_buffer_copy(data)
                    written = wintypes.DWORD(0)
                    if not winspool.WritePrinter(
                        handle, buffer, len(data), ctypes.byref(written)
                    ):
                        return PrintJobResult(
                            ok=False,
                            printer_name=printer_name,
                            message=(
                                "WritePrinter failed "
                                f"(Windows error {ctypes.get_last_error()})."
                            ),
                        )
                    if written.value != len(data):
                        return PrintJobResult(
                            ok=False,
                            printer_name=printer_name,
                            message=(
                                f"WritePrinter wrote {written.value} of "
                                f"{len(data)} bytes."
                            ),
                        )
                finally:
                    winspool.EndPagePrinter(handle)
                    winspool.EndDocPrinter(handle)
            finally:
                winspool.ClosePrinter(handle)
        return PrintJobResult(
            ok=True,
            printer_name=printer_name,
            message="Document sent to the Windows spooler (RAW).",
            job_id=last_job_id,
        )

    def send_test_print(self, printer_name: str) -> TestPrintResult:
        # Stage 4: the test print is a real (small) RAW spooler job
        # through print_document, replacing the Stage 2 stub.
        payload = (
            "Dealer Revenue Rescue - test page\r\n"
            f"Printer: {printer_name}\r\n"
            "If this page printed in full, the spooler path works.\r\n"
        )
        result = self.print_document(
            printer_name, payload, doc_id="test_page", title="Test page"
        )
        return TestPrintResult(
            ok=result.ok, printer_name=printer_name, message=result.message
        )


def get_printer_provider() -> PrinterProvider:
    """The provider for this machine: the Windows spooler provider on
    Windows, the fake provider everywhere else (tests/development)."""
    if sys.platform.startswith("win"):
        return WindowsPrinterProvider()
    return FakePrinterProvider()
