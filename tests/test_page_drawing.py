"""Pages the Windows driver path draws (page_drawing.py).

The Windows provider draws every print job through the printer's own
driver instead of sending raw PDF bytes. These tests prove, on any
machine, that every payload the app prints turns into the right
pages: the form image, every value at its place, the banner, and text
pages laid out for letter paper. The ctypes drawing itself runs only on
Windows (tests/test_printers_windows.py).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork import page_drawing as pd
from dealerpaperwork.demo_form import add_demo_form
from dealerpaperwork.doc_library import DocumentLibrary, sample_field_values
from dealerpaperwork.pdf_overlay import (
    PAGE_HEIGHT_PT,
    PT_PER_MM,
    render_overlay_pdf,
)
from dealerpaperwork.text_renderer import render_test_page


class DemoFormPagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = DocumentLibrary(Path(self.tmp.name) / "documents")
        self.document = add_demo_form(self.library)
        self.images = self.library.load_page_images(self.document)
        self.values = sample_field_values()

    def render(self, **kwargs):
        return render_overlay_pdf(self.document, self.values,
                                  page_images=self.images, **kwargs)

    def test_each_page_carries_its_own_form_image(self):
        pages = pd.pages_from_payload(self.render())
        self.assertEqual(len(pages), len(self.document.pages))
        for page, entry in zip(pages, self.document.pages):
            self.assertEqual(page.jpeg, self.images[entry.page].data)
            self.assertEqual(page.jpeg_components, self.images[entry.page].components)

    def test_every_value_lands_where_the_form_maps_it(self):
        pages = pd.pages_from_payload(self.render(offsets_mm={"x_mm": 1.5, "y_mm": -2.0}))
        expected = 0
        for placement in self.document.fields:
            value = str(self.values.get(placement.field_name) or "")
            if not value:
                continue
            expected += 1
            runs = [r for r in pages[placement.page - 1].runs if r.text == value]
            self.assertTrue(runs, f"{placement.field_name} not drawn")
            x = placement.x_mm * PT_PER_MM + 1.5 * PT_PER_MM
            y = PAGE_HEIGHT_PT - (placement.y_mm * PT_PER_MM + placement.font_size) + 2.0 * PT_PER_MM
            self.assertTrue(any(abs(r.x_pt - x) < 0.01 and abs(r.y_pt - y) < 0.01
                                and r.size_pt == float(placement.font_size) for r in runs),
                            f"{placement.field_name} misplaced")
        self.assertGreater(expected, 20)
        self.assertEqual(expected, sum(len(p.runs) for p in pages))

    def test_banner_is_bold_and_red_on_every_page(self):
        pages = pd.pages_from_payload(self.render(banner="SAMPLE"))
        for page in pages:
            banner = [r for r in page.runs if r.text == "*** SAMPLE ***"]
            self.assertEqual(len(banner), 1)
            self.assertEqual(banner[0].font, pd.FONT_BOLD)
            self.assertGreater(banner[0].rgb[0], banner[0].rgb[2])

    def test_text_with_brackets_backslashes_and_accents_survives(self):
        name = next(p.field_name for p in self.document.fields)
        self.values = dict(self.values, **{name: "José (Jr.) \\ Ñ"})
        pages = pd.pages_from_payload(self.render())
        self.assertIn("José (Jr.) \\ Ñ", [r.text for p in pages for r in p.runs])


class RefusalTest(unittest.TestCase):
    def test_a_pdf_from_elsewhere_is_refused(self):
        foreign = (b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
                   b"trailer\n<< >>\n%%EOF\n")
        with self.assertRaises(pd.PageDrawingError):
            pd.pages_from_payload(foreign)

    def test_other_bytes_are_refused(self):
        with self.assertRaises(pd.PageDrawingError):
            pd.pages_from_payload(b"custom")

    def test_a_damaged_app_pdf_is_refused(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        library = DocumentLibrary(Path(tmp.name) / "documents")
        document = add_demo_form(library)
        pdf = render_overlay_pdf(document, sample_field_values(),
                                 page_images=library.load_page_images(document))
        cut = pdf.replace(b"/Length ", b"/Length 1", 1)
        with self.assertRaises(pd.PageDrawingError):
            pd.pages_from_payload(cut)
        with self.assertRaises(pd.PageDrawingError):
            pd.pages_from_payload(pdf.replace(b" Tj ET", b" Tj 5 w ET", 1))


class TextPagesTest(unittest.TestCase):
    def test_test_page_prints_every_line_top_down(self):
        text = render_test_page(printer_name="Office", doc_id="d", title="Title")
        pages = pd.pages_from_payload(text)
        self.assertEqual(len(pages), 1)
        lines = [line for line in text.split("\r\n") if line]
        self.assertEqual([r.text for r in pages[0].runs], lines)
        ys = [r.y_pt for r in pages[0].runs]
        self.assertEqual(ys, sorted(ys, reverse=True))
        self.assertTrue(all(r.font == pd.FONT_MONO for r in pages[0].runs))

    def test_long_text_splits_into_pages_and_wraps(self):
        text = "\r\n".join(f"line {n}" for n in range(130)) + "\r\n" + "x" * 200
        pages = pd.pages_from_payload(text)
        self.assertEqual([len(p.runs) for p in pages],
                         [pd.TEXT_LINES_PER_PAGE, pd.TEXT_LINES_PER_PAGE, 130 - 2 * pd.TEXT_LINES_PER_PAGE + 3])
        self.assertEqual(pages[-1].runs[-1].text, "x" * 20)
        bottom = min(r.y_pt for p in pages for r in p.runs)
        self.assertGreaterEqual(bottom, pd.TEXT_MARGIN_PT - 1)

    def test_form_feed_starts_a_new_page(self):
        pages = pd.pages_from_payload("one\fTwo")
        self.assertEqual([[r.text for r in p.runs] for p in pages], [["one"], ["Two"]])


class DeviceMappingTest(unittest.TestCase):
    def test_letter_page_fills_the_paper_at_600_dpi(self):
        g = pd.DeviceGeometry(dpi_x=600, dpi_y=600, offset_x=100, offset_y=120)
        self.assertEqual(pd.page_rect(g), (-100, -120, 5100, 6600))

    def test_a_point_one_inch_in_and_down(self):
        g = pd.DeviceGeometry(dpi_x=300, dpi_y=600, offset_x=50, offset_y=60)
        self.assertEqual(pd.to_device(g, 72.0, PAGE_HEIGHT_PT - 72.0), (250, 540))

    def test_font_height_is_the_point_size(self):
        self.assertEqual(pd.font_height(pd.DeviceGeometry(600, 600), 12), -100)


if __name__ == "__main__":
    unittest.main()
