"""Tests for the shipped demo form (demo_form.py): the blank Florida HSMV
82040 title application, two page images with values already placed."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.demo_form import (
    DEMO_DOC_ID,
    DEMO_PLACEMENTS,
    add_demo_form,
    demo_page_paths,
)
from dealerpaperwork.doc_library import (
    CANONICAL_FIELDS,
    DocumentLibrary,
    DocumentLibraryError,
    sample_field_values,
    validate_placements,
)
from dealerpaperwork.jpeg_image import parse_jpeg
from dealerpaperwork.pdf_overlay import render_overlay_pdf


class TestDemoForm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = DocumentLibrary(Path(self.tmp.name) / "documents")

    def test_pages_are_full_letter_page_images_without_camera_data(self):
        for path in demo_page_paths():
            data = path.read_bytes()
            image = parse_jpeg(data)
            self.assertEqual((image.width_px, image.height_px), (2550, 3300), path.name)
            self.assertNotIn(b"Exif\x00", data[:65536], path.name)

    def test_every_placement_is_a_known_field_on_the_page(self):
        self.assertEqual(validate_placements(
            add_demo_form(self.library).fields, page_count=2), [])
        for field, page, x, y, size in DEMO_PLACEMENTS:
            self.assertIn(field, CANONICAL_FIELDS)
            self.assertIn(page, (1, 2))
            self.assertTrue(0 < x < 215.9 and 0 < y < 279.4, field)

    def test_added_as_a_draft_that_still_needs_approval(self):
        document = add_demo_form(self.library, created_by="mgr")
        self.assertEqual(document.doc_id, DEMO_DOC_ID)
        self.assertFalse(document.is_approved)
        self.assertEqual(len(document.pages), 2)
        self.assertEqual(len(document.fields), len(DEMO_PLACEMENTS))
        stored = self.library.load_page_images(document)
        for entry, path in zip(document.pages, demo_page_paths()):
            self.assertEqual(entry.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIn(entry.page, stored)

    def test_adding_it_twice_is_refused_and_keeps_the_first(self):
        first = add_demo_form(self.library)
        with self.assertRaises(DocumentLibraryError):
            add_demo_form(self.library)
        self.assertEqual(self.library.get_document(DEMO_DOC_ID).fields, first.fields)

    def test_sample_pdf_carries_both_pages_and_the_values(self):
        document = add_demo_form(self.library)
        pdf = render_overlay_pdf(document, sample_field_values(), banner="SAMPLE",
                                 page_images=self.library.load_page_images(document))
        self.assertIn(b"/Count 2", pdf)
        self.assertIn(b"(Sample Buyer)", pdf)
        self.assertIn(b"*** SAMPLE ***", pdf)


if __name__ == "__main__":
    unittest.main()
