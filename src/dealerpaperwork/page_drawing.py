"""Turn a print payload into pages a Windows printer driver can draw.

Why this exists: a printer only understands its own language (PCL,
PostScript, its maker's own). Sending a PDF straight to the spooler
works only on printers that read PDF themselves. Word, a browser and
every other Windows program instead draw each page through the
printer's Windows driver, and the driver speaks the printer's language.
This module gives the Windows provider (``printers.py``) the page
contents in a form it can draw that way:

* a PDF this app wrote (``pdf_overlay.py``): each page's form image
  (JPEG, drawn full page) and each text value at its position; and
* a text payload (packaging checklist, test pages): monospaced lines,
  wrapped and split into letter pages.

It reads ONLY the PDFs this app writes. The writer's dialect is
fixed and small, so the reader matches it exactly and refuses
anything else with ``PageDrawingError`` rather than guessing; a PDF
from anywhere else never reaches paper half drawn.

Standard library only. Nothing here touches Windows, so the whole
module runs and is tested on any machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

PAGE_WIDTH_PT = 612.0  # 8.5 in, matches pdf_overlay
PAGE_HEIGHT_PT = 792.0  # 11 in

# Text payload layout: Courier 10 pt on 12 pt lines, half-inch margins.
TEXT_FONT_SIZE = 10.0
TEXT_LINE_PT = 12.0
TEXT_MARGIN_PT = 36.0
TEXT_COLUMNS = 90  # 7.5 in of 10 pt Courier (6 pt per character)
TEXT_LINES_PER_PAGE = int((PAGE_HEIGHT_PT - 2 * TEXT_MARGIN_PT) // TEXT_LINE_PT)

FONT_REGULAR = "regular"  # pdf_overlay /F1 (Helvetica); drawn as Arial
FONT_BOLD = "bold"  # pdf_overlay /F2 (Helvetica-Bold); drawn as Arial Bold
FONT_MONO = "mono"  # text payloads; drawn as Courier New


class PageDrawingError(Exception):
    """The payload is not one this app wrote, so it is not drawn."""


@dataclass(frozen=True)
class TextRun:
    """One line of text. ``x_pt``/``y_pt`` are the baseline start in
    PDF points, measured from the page's bottom-left corner."""

    text: str
    x_pt: float
    y_pt: float
    size_pt: float
    font: str = FONT_REGULAR
    rgb: tuple = (0.0, 0.0, 0.0)


@dataclass
class DrawPage:
    """One letter page: an optional full-page JPEG, then text on top."""

    jpeg: Optional[bytes] = None
    jpeg_components: int = 0
    runs: list = field(default_factory=list)


# -- text payloads ----------------------------------------------------------


def pages_from_text(text: str) -> list[DrawPage]:
    """Lay a text payload out on letter pages, monospaced.

    Lines longer than the page are wrapped, a form feed starts a new
    page, and a page holds ``TEXT_LINES_PER_PAGE`` lines.
    """
    lines: list[object] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        parts = raw.split("\f")
        for index, part in enumerate(parts):
            if index:
                lines.append("\f")
            part = part.expandtabs(4).rstrip()
            if not part:
                lines.append("")
                continue
            while len(part) > TEXT_COLUMNS:
                lines.append(part[:TEXT_COLUMNS])
                part = part[TEXT_COLUMNS:]
            lines.append(part)
    while lines and lines[-1] == "":
        lines.pop()

    pages: list[DrawPage] = [DrawPage()]
    row = 0
    for line in lines:
        if line == "\f" or row >= TEXT_LINES_PER_PAGE:
            pages.append(DrawPage())
            row = 0
            if line == "\f":
                continue
        if line:
            baseline = PAGE_HEIGHT_PT - TEXT_MARGIN_PT - TEXT_FONT_SIZE - row * TEXT_LINE_PT
            pages[-1].runs.append(
                TextRun(line, TEXT_MARGIN_PT, baseline, TEXT_FONT_SIZE, FONT_MONO)
            )
        row += 1
    return pages


# -- PDFs this app wrote ----------------------------------------------------

_XREF_ENTRY = re.compile(rb"(\d{10}) 00000 n ")
_STARTXREF = re.compile(rb"startxref\n(\d+)\n%%EOF\n?$")
_STREAM_HEAD = re.compile(rb"<< ([^\n]*?) >>\nstream\n")
_TEXT_OP = re.compile(
    rb"BT /F([12]) ([0-9.]+) Tf ([0-9.]+) ([0-9.]+) ([0-9.]+) rg "
    rb"1 0 0 1 (-?[0-9.]+) (-?[0-9.]+) Tm \(((?:[^\\()]|\\[\\()])*)\) Tj ET"
)
_IMAGE_OP = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
_PAGE_DICT = re.compile(
    rb"^<< /Type /Page /Parent 2 0 R /MediaBox \[0 0 612 792\] "
    rb"/Resources << /Font << /F1 3 0 R /F2 4 0 R >>"
    rb"(?: /XObject << /Im1 (\d+) 0 R >>)? >> /Contents (\d+) 0 R >>$"
)
_PAGES_DICT = re.compile(rb"^<< /Type /Pages /Kids \[([0-9 R]*)\] /Count (\d+) >>$")
_LENGTH = re.compile(rb"/Length (\d+)")


def _unescape(raw: bytes) -> str:
    out = bytearray()
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte == 0x5C:  # backslash: the writer escapes only \ ( )
            out.append(raw[index + 1])
            index += 2
            continue
        out.append(byte)
        index += 1
    return out.decode("cp1252", errors="replace")


def _objects(data: bytes) -> dict[int, tuple[bytes, Optional[bytes]]]:
    """Every object by number, read through the PDF's own xref table:
    (dictionary text, stream bytes or None). Stream bytes are sliced by
    their /Length, so image bytes can never be mistaken for syntax."""
    tail = _STARTXREF.search(data)
    if not tail:
        raise PageDrawingError("Not a PDF this app wrote (no xref).")
    xref_at = int(tail.group(1))
    head = re.match(rb"xref\n0 (\d+)\n0000000000 65535 f \n", data[xref_at:])
    if not head:
        raise PageDrawingError("Not a PDF this app wrote (xref).")
    count = int(head.group(1))
    table = data[xref_at + head.end(): xref_at + head.end() + 20 * (count - 1)]
    offsets = [int(m.group(1)) for m in _XREF_ENTRY.finditer(table)]
    if len(offsets) != count - 1:
        raise PageDrawingError("The PDF's xref table is incomplete.")
    objects: dict[int, tuple[bytes, Optional[bytes]]] = {}
    for number, offset in enumerate(offsets, 1):
        marker = b"%d 0 obj\n" % number
        if data[offset: offset + len(marker)] != marker:
            raise PageDrawingError(f"PDF object {number} is not where the xref says.")
        start = offset + len(marker)
        stream_head = _STREAM_HEAD.match(data, start)
        if stream_head:
            length = _LENGTH.search(stream_head.group(1))
            if not length:
                raise PageDrawingError(f"PDF object {number} has no stream length.")
            begin = stream_head.end()
            end = begin + int(length.group(1))
            rest = data[end: end + 20]
            if not (rest.startswith(b"endstream\nendobj") or rest.startswith(b"\nendstream\nendobj")):
                raise PageDrawingError(f"PDF object {number} has the wrong stream length.")
            objects[number] = (stream_head.group(1), data[begin:end])
        else:
            close = data.find(b"\nendobj\n", start)
            if close < 0:
                raise PageDrawingError(f"PDF object {number} never ends.")
            objects[number] = (data[start:close], None)
    return objects


def _stream(objects: dict, number: int) -> tuple[bytes, bytes]:
    entry = objects.get(number)
    if entry is None or entry[1] is None:
        raise PageDrawingError(f"PDF object {number} is not a stream.")
    return entry


def pages_from_pdf(data: bytes) -> list[DrawPage]:
    """Read a PDF written by ``pdf_overlay`` into drawable pages."""
    if not data.startswith(b"%PDF-1.4\n") or not data.rstrip().endswith(b"%%EOF"):
        raise PageDrawingError("Not a PDF this app wrote.")
    objects = _objects(data)

    def plain(number: int) -> bytes:
        entry = objects.get(number)
        return entry[0] if entry and entry[1] is None else b""

    if plain(1) != b"<< /Type /Catalog /Pages 2 0 R >>":
        raise PageDrawingError("Not a PDF this app wrote (catalog).")
    pages_match = _PAGES_DICT.match(plain(2))
    if not pages_match:
        raise PageDrawingError("Not a PDF this app wrote (page list).")
    kids = [int(k) for k in re.findall(rb"(\d+) 0 R", pages_match.group(1))]
    if not kids or len(kids) != int(pages_match.group(2)):
        raise PageDrawingError("The PDF's page count does not match its pages.")

    pages: list[DrawPage] = []
    for kid in kids:
        page_match = _PAGE_DICT.match(plain(kid))
        if not page_match:
            raise PageDrawingError(f"PDF page object {kid} is not one this app writes.")
        image_obj, content_obj = page_match.groups()
        _, content = _stream(objects, int(content_obj))
        page = DrawPage()
        lines = [line for line in content.split(b"\n") if line.strip()]
        if image_obj is not None:
            if not lines or lines[0] != _IMAGE_OP:
                raise PageDrawingError("The form image is not drawn full page.")
            lines = lines[1:]
            head, jpeg = _stream(objects, int(image_obj))
            if b"/Filter /DCTDecode" not in head or not jpeg.startswith(b"\xff\xd8"):
                raise PageDrawingError("The form image is not a JPEG.")
            page.jpeg = jpeg
            page.jpeg_components = 1 if b"/DeviceGray" in head else 3
        for line in lines:
            match = _TEXT_OP.fullmatch(line)
            if not match:
                raise PageDrawingError("The PDF holds a drawing step this app does not write.")
            font, size, r, g, b, x, y, text = match.groups()
            page.runs.append(
                TextRun(
                    _unescape(text),
                    float(x),
                    float(y),
                    float(size),
                    FONT_BOLD if font == b"2" else FONT_REGULAR,
                    (float(r), float(g), float(b)),
                )
            )
        pages.append(page)
    return pages


def pages_from_payload(payload) -> list[DrawPage]:
    """Pages for any payload the print pipeline hands a provider."""
    if isinstance(payload, str):
        return pages_from_text(payload)
    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
        if data.startswith(b"%PDF"):
            return pages_from_pdf(data)
        raise PageDrawingError("The print job is bytes but not a PDF this app wrote.")
    raise PageDrawingError(f"Cannot print a {type(payload).__name__} payload.")


# -- device mapping ---------------------------------------------------------


@dataclass(frozen=True)
class DeviceGeometry:
    """What the printer driver reports about the page (GetDeviceCaps).

    ``offset_x``/``offset_y`` are the unprintable margins: device
    coordinates start at the printable corner, not the paper's edge.
    """

    dpi_x: int
    dpi_y: int
    offset_x: int = 0
    offset_y: int = 0


def to_device(geometry: DeviceGeometry, x_pt: float, y_pt: float) -> tuple[int, int]:
    """A PDF point (from the paper's bottom-left) to a device pixel
    (from the printable area's top-left)."""
    x = round(x_pt * geometry.dpi_x / 72.0) - geometry.offset_x
    y = round((PAGE_HEIGHT_PT - y_pt) * geometry.dpi_y / 72.0) - geometry.offset_y
    return x, y


def page_rect(geometry: DeviceGeometry) -> tuple[int, int, int, int]:
    """Where the full letter page lands: left, top, width, height."""
    left, top = to_device(geometry, 0.0, PAGE_HEIGHT_PT)
    width = round(PAGE_WIDTH_PT * geometry.dpi_x / 72.0)
    height = round(PAGE_HEIGHT_PT * geometry.dpi_y / 72.0)
    return left, top, width, height


def font_height(geometry: DeviceGeometry, size_pt: float) -> int:
    """GDI font height for a PDF font size: negative means the size is
    the character height, which is what a PDF font size is."""
    return -max(1, round(size_pt * geometry.dpi_y / 72.0))
