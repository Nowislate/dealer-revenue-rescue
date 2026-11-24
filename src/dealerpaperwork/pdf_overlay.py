"""Stdlib complete-page JPEG composition; legacy values-only bytes preserved.

Placements use top-left millimetres; images are anchored to letter pages.
JPEG bytes pass through unchanged using PDF DCTDecode. No filesystem I/O
occurs during composition.
"""
from __future__ import annotations

from typing import Optional
import hashlib
from .doc_library import validate_document_source, validate_placements, DocumentLibraryError
from .jpeg_image import JpegImage, parse_jpeg, JpegImageError

MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0
PT_PER_MM = POINTS_PER_INCH / MM_PER_INCH

LETTER_WIDTH_MM = 216.0
LETTER_HEIGHT_MM = 279.0
PAGE_WIDTH_PT = 612.0   # 8.5 in
PAGE_HEIGHT_PT = 792.0  # 11 in

# Banner wording for the overlay pages.
BANNERS = ("DRAFT", "SAMPLE")


def _escape_pdf_string(text: str) -> str:
    """Escape a string for a PDF literal string ``(...)``."""
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _encode(text: str) -> bytes:
    """Encode stream content as cp1252 (WinAnsi), replacing the rare
    character the built-in fonts cannot show."""
    return _escape_pdf_string(text).encode("cp1252", errors="replace")


def _placements_by_page(document) -> dict[int, list]:
    pages: dict[int, list] = {}
    for placement in document.fields:
        pages.setdefault(int(placement.page), []).append(placement)
    if not pages:
        pages[1] = []
    return pages


def _page_content(
    placements: list,
    values: dict,
    *,
    banner: Optional[str],
    offsets_mm: Optional[dict],
) -> bytes:
    """One page's content stream: banner, then each field value."""
    offsets = offsets_mm or {}
    dx_pt = float(offsets.get("x_mm", 0.0)) * PT_PER_MM
    dy_pt = float(offsets.get("y_mm", 0.0)) * PT_PER_MM
    chunks: list[bytes] = []
    if banner:
        # Top-centre banner in the bold face, sized to be unmissable.
        banner_text = f"*** {banner} ***"
        size = 20
        x = max(36.0, (PAGE_WIDTH_PT - len(banner_text) * size * 0.55) / 2)
        y = PAGE_HEIGHT_PT - 40
        chunks.append(
            b"BT /F2 %d Tf 0.55 0.08 0.1 rg 1 0 0 1 %.2f %.2f Tm (%s) Tj ET"
            % (size, x, y, _encode(banner_text))
        )
    for placement in placements:
        value = values.get(placement.field_name, "")
        if value is None:
            value = ""
        value = str(value)
        if not value:
            continue
        size = float(placement.font_size)
        x_pt = float(placement.x_mm) * PT_PER_MM + dx_pt
        # Flip: authored y is from the TOP; PDF y is from the BOTTOM.
        # Subtract the font size so the text's top sits near y_mm.
        y_pt = PAGE_HEIGHT_PT - (float(placement.y_mm) * PT_PER_MM + size) - dy_pt
        chunks.append(
            b"BT /F1 %.1f Tf 0 0 0 rg 1 0 0 1 %.2f %.2f Tm (%s) Tj ET"
            % (size, x_pt, y_pt, _encode(value))
        )
    return b"\n".join(chunks) + b"\n"


def _render_values_only(
    document,
    values: dict,
    *,
    banner: Optional[str] = None,
    offsets_mm: Optional[dict] = None,
) -> bytes:
    """Render the values-overlay PDF for one library document.

    ``document`` is a doc_library.DealerDocument; ``values`` maps
    canonical field names to display strings (see
    doc_library.resolve_field_values). ``banner`` is "DRAFT" or
    "SAMPLE" (or a custom marking such as a reprint note), drawn on
    every page. ``offsets_mm`` applies the per-printer/per-document
    calibration shift, exactly as for the text renderer.
    """
    pages = _placements_by_page(document)
    page_numbers = sorted(pages)

    # Object numbering: 1 catalog, 2 pages, 3/4 fonts, then per page a
    # Page object and its Contents object.
    objects: dict[int, bytes] = {}
    kids: list[int] = []
    next_obj = 5
    page_objs: dict[int, tuple[int, int]] = {}
    for page_number in page_numbers:
        page_obj = next_obj
        content_obj = next_obj + 1
        next_obj += 2
        page_objs[page_number] = (page_obj, content_obj)
        kids.append(page_obj)

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(b"%d 0 R" % k for k in kids)
        + b"] /Count "
        + str(len(kids)).encode("ascii")
        + b" >>"
    )
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"

    for page_number in page_numbers:
        page_obj, content_obj = page_objs[page_number]
        objects[page_obj] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            b"/Contents %d 0 R >>" % content_obj
        )
        stream = _page_content(
            pages[page_number], values,
            banner=banner, offsets_mm=offsets_mm,
        )
        objects[content_obj] = (
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\n"
            b"stream\n" + stream + b"endstream"
        )

    out = bytearray()
    out += b"%PDF-1.4\n"
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number
        out += objects[number]
        out += b"\nendobj\n"
    xref_at = len(out)
    count = max(objects) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for number in range(1, count):
        out += b"%010d 00000 n \n" % offsets[number]
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (count, xref_at)
    )
    return bytes(out)


class PdfOverlayError(Exception):
    """A complete document cannot be composed safely."""


def render_overlay_pdf(document, values: dict, *, banner: Optional[str] = None,
                       offsets_mm: Optional[dict] = None,
                       page_images: Optional[dict[int, JpegImage]] = None) -> bytes:
    try:
        validate_document_source(document)
        if document.form_source == "legacy_values_only":
            if page_images:
                raise PdfOverlayError("Legacy record received unexpected page images.")
            return _render_values_only(document, values, banner=banner, offsets_mm=offsets_mm)
        expected = {entry.page for entry in document.pages}
        if not page_images or set(page_images) != expected or any(type(k) is not int for k in page_images):
            raise PdfOverlayError("Every manifest page must have exactly one validated image.")
        errors = validate_placements(document.fields, page_count=len(document.pages))
        if errors:
            raise PdfOverlayError("; ".join(errors))
        for entry in document.pages:
            image = page_images[entry.page]
            parsed = parse_jpeg(image.data)
            if (image != parsed
                or (image.width_px, image.height_px, image.components) != (entry.width_px, entry.height_px, entry.components)
                or hashlib.sha256(image.data).hexdigest() != entry.sha256):
                raise PdfOverlayError(f"Page {entry.page}: image does not match manifest.")
    except (DocumentLibraryError, JpegImageError, AttributeError, TypeError) as exc:
        raise PdfOverlayError(str(exc)) from exc
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    }
    kids = []
    placements = _placements_by_page(document)
    for index, entry in enumerate(document.pages):
        page_obj = 5 + index * 3
        content_obj, image_obj = page_obj + 1, page_obj + 2
        kids.append(page_obj)
        image = page_images[entry.page]
        color = b"/DeviceGray" if image.components == 1 else b"/DeviceRGB"
        objects[image_obj] = (b"<< /Type /XObject /Subtype /Image /Width 2550 /Height 3300 "
            b"/BitsPerComponent 8 /ColorSpace " + color + b" /Filter /DCTDecode /Length "
            + str(len(image.data)).encode("ascii") + b" >>\nstream\n" + image.data + b"\nendstream")
        content = b"q 612 0 0 792 0 0 cm /Im1 Do Q\n" + _page_content(
            placements.get(entry.page, []), values, banner=banner, offsets_mm=offsets_mm)
        objects[content_obj] = b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"endstream"
        objects[page_obj] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> /XObject << /Im1 %d 0 R >> >> /Contents %d 0 R >>" % (image_obj, content_obj))
    objects[2] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count " + str(len(kids)).encode("ascii") + b" >>"
    out = bytearray()
    out += b"%PDF-1.4\n"
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number
        out += objects[number]
        out += b"\nendobj\n"
    xref_at = len(out)
    count = max(objects) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for number in range(1, count):
        out += b"%010d 00000 n \n" % offsets[number]
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (count, xref_at)
    )
    return bytes(out)


def write_overlay_pdf(path, document, values, **kwargs):
    """Render and write the overlay PDF to ``path``; returns the path."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(render_overlay_pdf(document, values, **kwargs))
    return target
