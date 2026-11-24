"""Click-to-place mapping (Decision 9: "click the spots, pick the fields").

Driven on the repo's synthetic fake forms (tests/fixtures/form_images).
The headless editor is tested through a real DocumentLibrary, so every
click goes through update_placements: the same validation, review
fingerprint and approval reset as typed millimetres. The window test
runs only where Tk can open a display (the Windows CI job has one).

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.audit import AuditLog
from dealerpaperwork.doc_library import (
    DocumentLibrary,
    DocumentLibraryError,
    FieldPlacement,
    document_fingerprint,
    sample_field_values,
)
from dealerpaperwork.document_setup import DocumentSetupActions
from dealerpaperwork.form_preview import decode_preview
from dealerpaperwork import placement_editor
from dealerpaperwork.placement_editor import PlacementEditor
from dealerpaperwork.users import UserStore

FIX = Path(__file__).resolve().parent / "fixtures" / "form_images"
DW, DH = 638, 826  # the window shows the 319 x 413 preview at 2x


class EditorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.library = DocumentLibrary(root / "documents")
        audit = AuditLog(root / "audit.jsonl")
        store = UserStore(root / "users.json", audit_log=audit)
        self.admin = store.create_user(actor=None, username="root", display_name="Root",
                                       role="admin", secret="pw-root")
        self.doc = self.library.add_document(
            source_paths=[FIX / "page-1.jpg", FIX / "page-2.jpg"],
            doc_id="fake-contract", title="Fake contract",
            when_it_prints=("retail_new",), created_by="root",
        )
        self.fingerprint = document_fingerprint(self.doc)
        self.saves = 0

    def save(self, fields):
        self.saves += 1
        updated = self.library.update_placements(
            "fake-contract", fields, expected_fingerprint=self.fingerprint)
        self.fingerprint = document_fingerprint(updated)
        return list(updated.fields)

    def editor(self):
        record = self.library.get_document("fake-contract", refresh=True)
        return PlacementEditor(record.fields, page_count=len(record.pages), save=self.save)

    def stored(self):
        return self.library.get_document("fake-contract", refresh=True).fields


class TestPlacing(EditorTestCase):
    def test_click_inside_the_buyer_box_saves_that_spot(self):
        # Buyer name box on fake page 1: top line at preview row 81.
        editor = self.editor()
        editor.place("buyer_name", 2 * 30, 2 * 84, DW, DH, 11)
        (placement,) = self.stored()
        self.assertEqual(placement.field_name, "buyer_name")
        self.assertEqual(placement.page, 1)
        self.assertAlmostEqual(placement.x_mm, 30 / 319 * 215.9, delta=0.05)
        self.assertAlmostEqual(placement.y_mm, 84 / 413 * 279.4, delta=0.05)
        self.assertEqual(placement.font_size, 11.0)
        self.assertEqual(editor.selected, 0)

    def test_sample_pdf_draws_the_value_where_it_was_clicked(self):
        editor = self.editor()
        editor.place("buyer_name", 60, 168, DW, DH, 10)
        sample = Path(self.tmp.name) / "sample.pdf"
        DocumentSetupActions(self.library).write_sample("fake-contract", sample)
        pdf = sample.read_bytes()
        (placement,) = self.stored()
        match = re.search(rb"1 0 0 1 ([\d.]+) ([\d.]+) Tm \(Sample Buyer\) Tj", pdf)
        self.assertIsNotNone(match)
        x_pt, y_pt = float(match.group(1)), float(match.group(2))
        pt = 72 / 25.4
        self.assertAlmostEqual(x_pt, placement.x_mm * pt, delta=0.01)
        self.assertAlmostEqual(y_pt, 792 - (placement.y_mm * pt + 10), delta=0.01)

    def test_second_page(self):
        editor = self.editor()
        editor.set_page(2)
        editor.place("vehicle_vin", 100, 100, DW, DH)
        self.assertEqual(self.stored()[0].page, 2)
        editor.set_page(1)
        self.assertEqual(editor.on_page(), [])
        with self.assertRaises(ValueError):
            editor.set_page(3)

    def test_unknown_field_is_refused_and_nothing_saved(self):
        editor = self.editor()
        with self.assertRaises(ValueError):
            editor.place("made_up_math", 10, 10, DW, DH)
        self.assertEqual(self.saves, 0)

    def test_every_change_resets_approval(self):
        editor = self.editor()
        editor.place("buyer_name", 60, 168, DW, DH)
        self.library.approve_document("fake-contract", self.admin,
                                      expected_fingerprint=self.fingerprint)
        self.fingerprint = document_fingerprint(self.library.get_document("fake-contract", refresh=True))
        self.assertTrue(self.library.get_document("fake-contract", refresh=True).is_approved)
        editor.nudge(0.5, 0)
        self.assertFalse(self.library.get_document("fake-contract", refresh=True).is_approved)


class TestSelectingAndMoving(EditorTestCase):
    def placed(self):
        editor = self.editor()
        editor.place("buyer_name", 60, 168, DW, DH, 10)
        editor.place("sales_tax_total", 60, 400, DW, DH, 10)
        editor.selected = None
        return editor

    def test_click_on_a_value_selects_it(self):
        editor = self.placed()
        left, top, right, bottom = editor.box(editor.fields[0], DW, DH)
        self.assertEqual(editor.hit((left + right) / 2, (top + bottom) / 2, DW, DH), 0)
        self.assertIsNone(editor.hit(600, 800, DW, DH))
        # The box is as wide as the sample text it will print.
        self.assertGreater(right - left, 4 * (bottom - top))

    def test_drag_saves_once_on_drop(self):
        editor = self.placed()
        editor.selected = 0
        before = self.saves
        editor.drag_to(100, 200, DW, DH)
        editor.drag_to(120, 220, DW, DH)
        self.assertEqual(self.saves, before)          # nothing written mid-drag
        self.assertEqual(editor.on_page()[0][1].x_mm, round(120 / DW * 215.9, 1))
        self.assertEqual(self.stored()[0].x_mm, round(60 / DW * 215.9, 1))
        editor.end_drag()
        self.assertEqual(self.saves, before + 1)
        self.assertEqual(self.stored()[0].x_mm, round(120 / DW * 215.9, 1))

    def test_nudge_and_clamp(self):
        editor = self.placed()
        editor.selected = 0
        x0 = editor.fields[0].x_mm
        editor.nudge(0.5, 0)
        self.assertAlmostEqual(self.stored()[0].x_mm, round(x0 + 0.5, 1))
        editor.nudge(-500, -500)
        self.assertEqual((self.stored()[0].x_mm, self.stored()[0].y_mm), (0.0, 0.0))

    def test_unmoved_value_writes_nothing(self):
        editor = self.placed()
        editor.selected = 0
        before = self.saves
        editor.nudge(0, 0)
        self.assertEqual(self.saves, before)

    def test_remove_and_font(self):
        editor = self.placed()
        editor.selected = 1
        editor.set_selected_font(14)
        self.assertEqual(self.stored()[1].font_size, 14.0)
        editor.remove_selected()
        self.assertEqual([f.field_name for f in self.stored()], ["buyer_name"])
        self.assertIsNone(editor.selected)

    def test_refused_save_leaves_screen_and_record_unchanged(self):
        editor = self.placed()
        editor.selected = 0
        self.fingerprint = "stale"  # someone else changed the record
        with self.assertRaises(DocumentLibraryError):
            editor.nudge(1, 1)
        self.assertEqual(editor.fields[0].x_mm, self.stored()[0].x_mm)

    def test_nothing_selected(self):
        editor = self.editor()
        for action in (lambda: editor.nudge(1, 0), editor.remove_selected,
                       lambda: editor.move_selected_to(1, 1, DW, DH)):
            with self.assertRaises(ValueError):
                action()


def _tk_root():
    if not placement_editor.TK_AVAILABLE:
        return None
    try:
        root = placement_editor.tk.Tk()
    except Exception:  # no display (TclError), or a stand-in tkinter
        return None
    root.withdraw()
    return root


class TestWindow(EditorTestCase):
    def setUp(self):
        super().setUp()
        self.root = _tk_root()
        if self.root is None:
            self.skipTest("No display for Tk here; the Windows CI job runs this.")
        self.addCleanup(self.root.destroy)

    def test_click_places_drag_moves_delete_removes(self):
        images = self.library.load_page_images(self.doc)
        window = placement_editor.PlacementWindow(
            self.root, title="Fake contract", page_images=images,
            fields=[], save=self.save)
        self.root.update()
        self.assertEqual(window.size, (638, 826))
        event = lambda x, y: SimpleNamespace(x=x, y=y)
        window.field_var.set("Buyer: Buyer name [buyer_name]")
        window._press(event(60, 168))
        self.assertEqual([f.field_name for f in self.stored()], ["buyer_name"])
        first = self.stored()[0]
        # The click maps through the canvas (inside its 1 px border).
        x0, y0 = window.canvas.canvasx(60), window.canvas.canvasy(168)
        self.assertAlmostEqual(first.x_mm, x0 / 638 * 215.9, delta=0.05)
        self.assertAlmostEqual(first.y_mm, y0 / 826 * 279.4, delta=0.05)
        window._press(event(70, 172))      # grab the placed value
        window._motion(event(170, 272))
        window._release(event(170, 272))
        moved = self.stored()[0]
        # Dragged 100 px right and down: the value moves the same
        # distance (the grab offset is kept), within rounding.
        self.assertAlmostEqual(moved.x_mm - first.x_mm, 100 / 638 * 215.9, delta=0.11)
        self.assertAlmostEqual(moved.y_mm - first.y_mm, 100 / 826 * 279.4, delta=0.11)
        window._nudge(0.5, 0)
        self.assertAlmostEqual(self.stored()[0].x_mm, moved.x_mm + 0.5, delta=0.01)
        window._remove()
        self.assertEqual(self.stored(), [])
        window.close()


if __name__ == "__main__":
    unittest.main()
