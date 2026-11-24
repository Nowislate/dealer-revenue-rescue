"""Tests for the Stage 5 values-overlay PDF writer (pdf_overlay.py).

The overlay is a minimal standard-library PDF: letter pages, one per
placement page, each field's value drawn at its spot (input x/y in
millimetres from the TOP-left; PDF origin is bottom-left, so y is
flipped). Merging onto the dealer's own form PDF is out of scope --
the dealer's print path does that.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.doc_library import DealerDocument, FieldPlacement
from dealerpaperwork.pdf_overlay import render_overlay_pdf, write_overlay_pdf

VALUES = {
    "buyer_name": "Ada Buyer",
    "vehicle_vin": "4S4BTANC0T3000001",
    "sales_tax_total": "1646.94",
}


def document(placements, doc_id="we-owe"):
    return DealerDocument(
        doc_id=doc_id,
        title="We Owe",
        source_filename="we-owe.pdf",
        stored_name=f"{doc_id}.pdf",
        when_it_prints=("retail_new",),
        fields=placements,
    )


def y_for_value(pdf: bytes, value: str) -> float:
    """Extract the text-positioning y used for ``value``'s line."""
    text = pdf.decode("latin-1")
    match = re.search(
        r"1 0 0 1 ([\d.]+) ([\d.]+) Tm \(%s\) Tj" % re.escape(value), text
    )
    assert match, f"value {value!r} not placed in the content stream"
    return float(match.group(2))


class TestOverlayPdf(unittest.TestCase):
    def test_valid_minimal_pdf_with_values(self):
        pdf = render_overlay_pdf(
            document([
                FieldPlacement("buyer_name", 1, 25.0, 40.0, 11),
                FieldPlacement("vehicle_vin", 1, 25.0, 55.0, 10),
            ]),
            VALUES,
        )
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"Ada Buyer", pdf)
        self.assertIn(b"4S4BTANC0T3000001", pdf)
        # Letter media box: 612 x 792 pt.
        self.assertIn(b"/MediaBox [0 0 612 792]", pdf)

    def test_y_is_flipped_from_top_left_input(self):
        near_top = render_overlay_pdf(
            document([FieldPlacement("buyer_name", 1, 25.0, 10.0, 11)]), VALUES
        )
        near_bottom = render_overlay_pdf(
            document([FieldPlacement("buyer_name", 1, 25.0, 200.0, 11)]), VALUES
        )
        # A small top-left y (near the top of the page) must become a
        # LARGE PDF y (PDF origin is bottom-left).
        self.assertGreater(
            y_for_value(near_top, "Ada Buyer"),
            y_for_value(near_bottom, "Ada Buyer"),
        )

    def test_banners(self):
        doc = document([FieldPlacement("buyer_name", 1, 25.0, 40.0, 11)])
        self.assertIn(b"DRAFT", render_overlay_pdf(doc, VALUES, banner="DRAFT"))
        self.assertIn(b"SAMPLE", render_overlay_pdf(doc, VALUES, banner="SAMPLE"))
        plain = render_overlay_pdf(doc, VALUES)
        self.assertNotIn(b"DRAFT", plain)
        self.assertNotIn(b"SAMPLE", plain)

    def test_special_characters_escaped(self):
        pdf = render_overlay_pdf(
            document([FieldPlacement("buyer_name", 1, 25.0, 40.0, 11)]),
            {"buyer_name": "Buyer (Test) \\ Co"},
        )
        self.assertIn(rb"Buyer \(Test\) \\ Co", pdf)
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_one_page_per_placement_page(self):
        pdf = render_overlay_pdf(
            document([
                FieldPlacement("buyer_name", 1, 25.0, 40.0, 11),
                FieldPlacement("sales_tax_total", 2, 25.0, 40.0, 11),
                FieldPlacement("vehicle_vin", 2, 25.0, 55.0, 10),
            ]),
            VALUES,
        )
        self.assertIn(b"/Count 2", pdf)
        self.assertIn(b"1646.94", pdf)

    def test_write_overlay_pdf_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "overlay.pdf"
            written = write_overlay_pdf(
                target,
                document([FieldPlacement("buyer_name", 1, 25.0, 40.0, 11)]),
                VALUES,
                banner="SAMPLE",
            )
            self.assertEqual(written, target)
            data = target.read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"))
            self.assertIn(b"SAMPLE", data)


if __name__ == "__main__":
    unittest.main()
