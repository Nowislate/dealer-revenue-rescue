"""Form-page preview and click geometry for click-to-place (Decision 9).

The fixtures are the repo's synthetic fake forms (tests/fixtures/
form_images, no dealer or customer source). The pinned preview hashes
were checked outside the repo against an independent decoder (Pillow,
full decode then 8 x 8 box average): every interior preview pixel was
within 2 of 255 levels on all four fixtures. Variants made outside the
repo (4:2:0 and 4:4:4 color, progressive with successive approximation,
restart markers, grayscale) matched the same way.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import hashlib
import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.form_preview import (
    PAGE_HEIGHT_MM,
    PAGE_WIDTH_MM,
    PreviewError,
    decode_preview,
    display_to_mm,
    encode_gray_png,
    font_height_display,
    mm_to_display,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "form_images"

PINNED = {
    "page-1": "99fcb3c43a829015c17b6df0c037c388070c910c128f10f511e2458120120a6f",
    "page-2": "ee158c75c18d289c97e93a415e346694d2e5c421ddc316dbbf8a963c56c642e1",
    "page-3": "199c3c5f0968c9826f46b1584cdc776689c4678124afc84d1ca3090f198cc754",
    "progressive": "1437bc62b2b767683b9cb7701c51fa39d0c1413ca1110d3c78e35685be2e60b6",
}


def preview(name):
    return decode_preview((FIXTURES / f"{name}.jpg").read_bytes())


def first_dark_row(p, x, start):
    for y in range(start, p.height):
        if p.pixels[y * p.width + x] < 200:
            return y
    return None


class TestDecode(unittest.TestCase):
    def test_every_fixture_decodes_to_the_pinned_preview(self):
        for name, digest in PINNED.items():
            p = preview(name)
            self.assertEqual((p.width, p.height), (319, 413), name)
            self.assertEqual(hashlib.sha256(p.pixels).hexdigest(), digest, name)

    def test_progressive_matches_baseline_of_the_same_page(self):
        # progressive.jpg is page 2 encoded progressively.
        base, prog = preview("page-2").pixels, preview("progressive").pixels
        diff = [abs(a - b) for a, b in zip(base, prog)]
        self.assertLessEqual(max(diff), 2)

    def test_page_is_mostly_white_with_dark_box_lines(self):
        p = preview("page-1")
        white = sum(1 for v in p.pixels if v > 230)
        self.assertGreater(white / len(p.pixels), 0.85)
        self.assertIsNotNone(first_dark_row(p, 160, 70))

    def test_not_a_form_page_is_refused(self):
        with self.assertRaises(PreviewError):
            decode_preview(b"not a jpeg")

    def test_png_is_well_formed(self):
        p = preview("page-1")
        png = p.to_png()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (319, 413))
        idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
        rows = zlib.decompress(idat)
        self.assertEqual(len(rows), 413 * 320)
        self.assertEqual(rows[1:320], p.pixels[:319])
        with self.assertRaises(ValueError):
            encode_gray_png(2, 2, b"\x00")


class TestGeometry(unittest.TestCase):
    def test_corners_and_centre(self):
        self.assertEqual(display_to_mm(0, 0, 638, 826), (0.0, 0.0))
        self.assertEqual(display_to_mm(319, 413, 638, 826), (108.0, 139.7))
        # The far corner is clamped to the validator's letter limits.
        self.assertEqual(display_to_mm(638, 826, 638, 826), (215.9, 279.0))
        self.assertEqual(display_to_mm(-5, 900, 638, 826), (0.0, 279.0))

    def test_round_trip_within_a_tenth_of_a_mm(self):
        for x_mm, y_mm in ((12.3, 45.6), (200.0, 270.1), (0.4, 0.4)):
            x, y = mm_to_display(x_mm, y_mm, 638, 826)
            back = display_to_mm(x, y, 638, 826)
            self.assertAlmostEqual(back[0], x_mm, delta=0.05)
            self.assertAlmostEqual(back[1], y_mm, delta=0.05)

    def test_page_scale_is_300_dpi_letter(self):
        self.assertAlmostEqual(PAGE_WIDTH_MM, 215.9)
        self.assertAlmostEqual(PAGE_HEIGHT_MM, 279.4)
        # 10 pt text is 3.53 mm tall: on an 826-pixel preview, 10.43 px.
        self.assertAlmostEqual(font_height_display(10, 826), 10.43, places=2)

    def test_click_on_a_box_line_lands_on_that_line_in_mm(self):
        # The Buyer name box on fake form page 1: its top line in the
        # preview, clicked, must land at the same height on the page.
        p = preview("page-1")
        row = first_dark_row(p, 160, 70)
        _x_mm, y_mm = display_to_mm(160 * 2, row * 2, p.width * 2, p.height * 2)
        line_mm = (row + 0.5) * 8 * 25.4 / 300  # block centre
        self.assertAlmostEqual(y_mm, line_mm, delta=0.7)

    def test_zero_size_is_refused(self):
        with self.assertRaises(ValueError):
            display_to_mm(1, 1, 0, 826)


if __name__ == "__main__":
    unittest.main()
