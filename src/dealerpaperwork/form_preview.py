"""On-screen preview of a form page, for click-to-place mapping.

Standard library only (no imaging package), so the preview is built
from the JPEG's DC coefficients: each 8 x 8 pixel block becomes one
preview pixel holding the block's average brightness. A 2550 x 3300
form page becomes a 319 x 413 grayscale preview, about 0.68 mm per
preview pixel -- enough to see the boxes and click a spot. Only the
luma (brightness) component is kept; color does not matter for
placement.

The printed page never uses this preview: composition still embeds
the original JPEG bytes unchanged (pdf_overlay.py). If a preview
cannot be decoded, the mapping screen says so and typed millimetres
still work.

Placement geometry lives here too: one function converts a click on
the shown preview to the page millimetres the overlay writer uses,
and one converts back to draw a placement on the preview.
"""
from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass

from .jpeg_image import parse_jpeg

# The page image is drawn over the whole letter page (612 x 792 pt);
# a 2550-pixel-wide image is therefore 2550 / 300 in = 215.9 mm.
PAGE_WIDTH_PX = 2550
PAGE_HEIGHT_PX = 3300
MM_PER_PAGE_PX = 25.4 / 300.0
PAGE_WIDTH_MM = PAGE_WIDTH_PX * MM_PER_PAGE_PX    # 215.9
PAGE_HEIGHT_MM = PAGE_HEIGHT_PX * MM_PER_PAGE_PX  # 279.4
# The placement validator's letter limits (doc_library).
MAX_X_MM = 216.0
MAX_Y_MM = 279.0
PT_PER_MM = 72.0 / 25.4


class PreviewError(Exception):
    """The page could not be turned into a preview."""


@dataclass(frozen=True)
class PagePreview:
    """Grayscale preview: ``pixels`` is width*height bytes, row-major."""

    width: int
    height: int
    pixels: bytes

    def to_png(self) -> bytes:
        return encode_gray_png(self.width, self.height, self.pixels)

    def to_png_base64(self) -> str:
        """For ``tkinter.PhotoImage(data=...)`` (Tk 8.6 reads PNG)."""
        return base64.b64encode(self.to_png()).decode("ascii")


# ---------------------------------------------------------------------------
# Geometry: preview display <-> page millimetres
# ---------------------------------------------------------------------------


def display_to_mm(x: float, y: float, display_width: float, display_height: float) -> tuple[float, float]:
    """A click at (x, y) on a preview shown at display_width x
    display_height pixels -> (x_mm, y_mm) from the page's top-left,
    rounded to 0.1 mm and kept on the page."""
    if display_width <= 0 or display_height <= 0:
        raise ValueError("The preview has no size yet.")
    x_mm = x / display_width * PAGE_WIDTH_MM
    y_mm = y / display_height * PAGE_HEIGHT_MM
    x_mm = min(max(x_mm, 0.0), MAX_X_MM)
    y_mm = min(max(y_mm, 0.0), MAX_Y_MM)
    return round(x_mm, 1), round(y_mm, 1)


def mm_to_display(x_mm: float, y_mm: float, display_width: float, display_height: float) -> tuple[float, float]:
    """Inverse of display_to_mm (before rounding)."""
    return (x_mm / PAGE_WIDTH_MM * display_width,
            y_mm / PAGE_HEIGHT_MM * display_height)


def font_height_display(font_size_pt: float, display_height: float) -> float:
    """How tall a value of this point size is on the shown preview."""
    return font_size_pt / PT_PER_MM / PAGE_HEIGHT_MM * display_height


# ---------------------------------------------------------------------------
# PNG writer (grayscale, 8-bit)
# ---------------------------------------------------------------------------


def encode_gray_png(width: int, height: int, pixels: bytes) -> bytes:
    if len(pixels) != width * height:
        raise ValueError("Pixel count does not match the size.")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    rows = b"".join(b"\x00" + pixels[r * width:(r + 1) * width] for r in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6))
            + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# DC-only JPEG decoding
# ---------------------------------------------------------------------------

_ZIGZAG_DC = 0  # the DC coefficient is first in zig-zag order


class _Huffman:
    """Lookup on a 16-bit peek: table[bits] = (symbol, code length)."""

    __slots__ = ("table",)

    def __init__(self, counts: bytes, symbols: bytes):
        table = [None] * 65536
        code = 0
        k = 0
        for length in range(1, 17):
            for _ in range(counts[length - 1]):
                if k >= len(symbols):
                    raise PreviewError("Malformed Huffman table.")
                shift = 16 - length
                start = code << shift
                entry = (symbols[k], length)
                for index in range(start, start + (1 << shift)):
                    table[index] = entry
                code += 1
                k += 1
            code <<= 1
        self.table = table


class _Bits:
    """MSB-first bit reader over one restart interval (unstuffed)."""

    __slots__ = ("data", "pos", "acc", "count")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.acc = 0
        self.count = 0

    def _fill(self, need: int) -> None:
        data = self.data
        while self.count < need:
            if self.pos < len(data):
                self.acc = (self.acc << 8) | data[self.pos]
                self.pos += 1
            else:
                self.acc = (self.acc << 8) | 0xFF  # pad with ones
            self.count += 8

    def decode(self, huffman: _Huffman) -> int:
        if self.count < 16:
            self._fill(16)
        peek = (self.acc >> (self.count - 16)) & 0xFFFF
        entry = huffman.table[peek]
        if entry is None:
            raise PreviewError("Corrupt image data (bad Huffman code).")
        symbol, length = entry
        self.count -= length
        self.acc &= (1 << self.count) - 1
        return symbol

    def receive(self, size: int) -> int:
        if size == 0:
            return 0
        if self.count < size:
            self._fill(size)
        self.count -= size
        value = self.acc >> self.count
        self.acc &= (1 << self.count) - 1
        if value < (1 << (size - 1)):  # sign-extend (JPEG EXTEND)
            value -= (1 << size) - 1
        return value

    def skip(self, size: int) -> None:
        if self.count < size:
            self._fill(size)
        self.count -= size
        self.acc &= (1 << self.count) - 1


@dataclass
class _Component:
    cid: int
    h: int
    v: int
    tq: int


def _scan_segments(data: bytes, pos: int) -> tuple[list[bytes], int]:
    """Entropy-coded data from ``pos`` to the next real marker, split at
    restart markers and unstuffed. Returns (segments, next marker pos)."""
    segments = []
    start = pos
    while True:
        at = data.find(b"\xff", pos)
        if at < 0 or at + 1 >= len(data):
            raise PreviewError("Image data ends early.")
        code = data[at + 1]
        if code == 0x00:
            pos = at + 2
            continue
        if code == 0xFF:  # fill byte
            pos = at + 1
            continue
        segments.append(data[start:at].replace(b"\xff\x00", b"\xff"))
        if 0xD0 <= code <= 0xD7:
            pos = start = at + 2
            continue
        return segments, at


def decode_preview(data: bytes) -> PagePreview:
    """DC-only grayscale preview of a validated form-page JPEG."""
    try:
        parse_jpeg(data)
    except Exception as exc:
        raise PreviewError(str(exc)) from exc

    qt: dict[int, list[int]] = {}
    dc_tables: dict[int, _Huffman] = {}
    ac_tables: dict[int, _Huffman] = {}
    components: list[_Component] = []
    width = height = 0
    progressive = False
    restart = 0
    out = None
    out_w = out_h = 0
    luma_q0 = 1

    pos = 2
    while pos < len(data):
        while data[pos] == 0xFF and data[pos + 1] == 0xFF:
            pos += 1
        marker = data[pos + 1]
        pos += 2
        if marker == 0xD9:
            break
        size = int.from_bytes(data[pos:pos + 2], "big")
        payload = data[pos + 2:pos + size]
        segment_end = pos + size
        if marker == 0xDB:
            i = 0
            while i < len(payload):
                precision, tid = payload[i] >> 4, payload[i] & 15
                i += 1
                count = 128 if precision else 64
                raw = payload[i:i + count]
                qt[tid] = (list(struct.unpack(">64H", raw)) if precision else list(raw))
                i += count
        elif marker == 0xC4:
            i = 0
            while i < len(payload):
                cls, tid = payload[i] >> 4, payload[i] & 15
                counts = payload[i + 1:i + 17]
                total = sum(counts)
                symbols = payload[i + 17:i + 17 + total]
                (ac_tables if cls else dc_tables)[tid] = _Huffman(counts, symbols)
                i += 17 + total
        elif marker == 0xDD:
            restart = int.from_bytes(payload[:2], "big")
        elif marker in (0xC0, 0xC2):
            progressive = marker == 0xC2
            height, width = struct.unpack(">HH", payload[1:5])
            n = payload[5]
            for k in range(n):
                cid, hv, tq = payload[6 + 3 * k:9 + 3 * k]
                components.append(_Component(cid, hv >> 4, hv & 15, tq))
        elif marker == 0xDA:
            n = payload[0]
            scan = []
            for k in range(n):
                cid, tables = payload[1 + 2 * k], payload[2 + 2 * k]
                comp = next(c for c in components if c.cid == cid)
                scan.append((comp, tables >> 4, tables & 15))
            ss, se, ahal = payload[1 + 2 * n], payload[2 + 2 * n], payload[3 + 2 * n]
            ah, al = ahal >> 4, ahal & 15
            segments, next_marker = _scan_segments(data, segment_end)
            luma = components[0]
            wanted = (out is None and ss == 0 and ah == 0
                      and any(c is luma for c, _d, _a in scan))
            if wanted:
                if luma.tq not in qt:
                    raise PreviewError("Missing quantization table.")
                luma_q0 = qt[luma.tq][_ZIGZAG_DC]
                hmax = max(c.h for c in components)
                vmax = max(c.v for c in components)
                out_w = -(-(-(-width * luma.h // hmax)) // 8)
                out_h = -(-(-(-height * luma.v // vmax)) // 8)
                out = _decode_dc_scan(
                    scan, segments, restart, width, height, hmax, vmax,
                    luma, out_w, out_h, dc_tables, ac_tables,
                    progressive=progressive, se=se, al=al,
                )
                if not progressive or out is not None:
                    break
            pos = next_marker
            continue
        pos = segment_end

    if out is None:
        raise PreviewError("No brightness data found in the image.")
    pixels = bytes(_clamp(round(dc * luma_q0 / 8) + 128) for dc in out)
    return PagePreview(out_w, out_h, pixels)


def _clamp(value: int) -> int:
    return 0 if value < 0 else 255 if value > 255 else value


def _decode_dc_scan(scan, segments, restart, width, height, hmax, vmax,
                    luma, out_w, out_h, dc_tables, ac_tables, *,
                    progressive, se, al):
    """Decode one scan, keeping the luma DC value of every block."""
    out = [0] * (out_w * out_h)
    skip_ac = not progressive and se > 0
    for comp, td, ta in scan:
        if td not in dc_tables or (skip_ac and ta not in ac_tables):
            raise PreviewError("Missing Huffman table.")

    if len(scan) == 1:
        comp = scan[0][0]
        bw = -(-(-(-width * comp.h // hmax)) // 8)
        bh = -(-(-(-height * comp.v // vmax)) // 8)
        units = [((comp, td, ta), bx, by) for by in range(bh) for bx in range(bw)]
        layout = None
    else:
        mcux = -(-width // (8 * hmax))
        mcuy = -(-height // (8 * vmax))
        units = None
        layout = (mcux, mcuy)

    seg_iter = iter(segments)
    bits = _Bits(next(seg_iter, b""))
    preds = {}
    interval_left = restart

    def new_interval():
        nonlocal bits, interval_left
        bits = _Bits(next(seg_iter, b""))
        preds.clear()
        interval_left = restart

    def block(entry, bx, by):
        comp, td, ta = entry
        s = bits.decode(dc_tables[td])
        diff = bits.receive(s) if s else 0
        value = preds.get(comp.cid, 0) + diff
        preds[comp.cid] = value
        if skip_ac:
            table = ac_tables[ta]
            k = 1
            while k < 64:
                rs = bits.decode(table)
                r, size = rs >> 4, rs & 15
                if size == 0:
                    if r != 15:
                        break
                    k += 16
                    continue
                k += r
                bits.skip(size)
                k += 1
        if comp is luma and bx < out_w and by < out_h:
            out[by * out_w + bx] = value << al

    if units is not None:
        for entry, bx, by in units:
            if restart and interval_left == 0:
                new_interval()
            block(entry, bx, by)
            interval_left -= 1
    else:
        mcux, mcuy = layout
        for my in range(mcuy):
            for mx in range(mcux):
                if restart and interval_left == 0:
                    new_interval()
                for entry in scan:
                    comp = entry[0]
                    for v in range(comp.v):
                        for h in range(comp.h):
                            block(entry, mx * comp.h + h, my * comp.v + v)
                interval_left -= 1
    return out
