"""Tests for Stage 4 print configuration (print_config.py): copy sets
and alignment calibration, persisted locally.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.pack import DOC_CLASS_LAW_CONTRACT, DOC_CLASS_STANDARD_LETTER
from dealerpaperwork.print_config import CopySet, PrintConfig

PRINTER = "FakePrinter - F&I (Long Stock)"


class PrintConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "print_config.json"
        self.config = PrintConfig(path=self.path)


class TestCopySets(PrintConfigTestCase):
    def test_defaults_by_class(self):
        contract = self.config.copy_set_for(
            "retail_installment_contract", DOC_CLASS_LAW_CONTRACT
        )
        self.assertEqual((contract.buyer, contract.dealer, contract.lender), (1, 1, 1))
        self.assertEqual(contract.total, 3)
        letter = self.config.copy_set_for("buyers_order", DOC_CLASS_STANDARD_LETTER)
        self.assertEqual(letter.total, 2)

    def test_worksheet_override_is_dealer_only(self):
        worksheet = self.config.copy_set_for(
            "deal_worksheet", DOC_CLASS_STANDARD_LETTER
        )
        self.assertEqual(worksheet.total, 1)
        self.assertEqual(worksheet.recipient_order(), ("dealer",))

    def test_recipient_order_is_buyer_dealer_lender(self):
        cs = CopySet(buyer=1, dealer=1, lender=1)
        self.assertEqual(cs.recipient_order(), ("buyer", "dealer", "lender"))

    def test_setting_an_override_changes_the_effective_set(self):
        self.config.set_copy_set(
            CopySet(buyer=2, dealer=1, lender=1), doc_id="buyers_order"
        )
        self.assertEqual(
            self.config.total_copies_for("buyers_order", DOC_CLASS_STANDARD_LETTER), 4
        )

    def test_copy_sets_survive_save_load(self):
        self.config.set_copy_set(
            CopySet(buyer=3, dealer=0, lender=0), doc_id="buyers_order"
        )
        loaded = PrintConfig.load(self.path)
        self.assertEqual(
            loaded.total_copies_for("buyers_order", DOC_CLASS_STANDARD_LETTER), 3
        )


class TestCalibration(PrintConfigTestCase):
    def test_unset_offsets_are_zero(self):
        self.assertEqual(
            self.config.get_offsets(PRINTER, "buyers_order"),
            {"x_mm": 0.0, "y_mm": 0.0},
        )

    def test_set_and_read_back(self):
        self.config.set_offsets(PRINTER, "buyers_order", 1.5, -0.5)
        self.assertEqual(
            self.config.get_offsets(PRINTER, "buyers_order"),
            {"x_mm": 1.5, "y_mm": -0.5},
        )

    def test_nudge_moves_by_half_mm_steps(self):
        self.config.set_offsets(PRINTER, "buyers_order", 1.0, 0.0)
        after = self.config.nudge(PRINTER, "buyers_order", dx_steps=1, dy_steps=-2)
        self.assertEqual(after, {"x_mm": 1.5, "y_mm": -1.0})
        self.assertEqual(
            self.config.get_offsets(PRINTER, "buyers_order"), after
        )

    def test_offsets_are_per_printer_and_document(self):
        self.config.set_offsets(PRINTER, "buyers_order", 2.0, 2.0)
        self.assertEqual(
            self.config.get_offsets(PRINTER, "deal_worksheet"),
            {"x_mm": 0.0, "y_mm": 0.0},
        )
        self.assertEqual(
            self.config.get_offsets("Other Printer", "buyers_order"),
            {"x_mm": 0.0, "y_mm": 0.0},
        )

    def test_offsets_persist_across_save_load(self):
        self.config.set_offsets(PRINTER, "retail_installment_contract", -1.0, 2.5)
        loaded = PrintConfig.load(self.path)
        self.assertEqual(
            loaded.get_offsets(PRINTER, "retail_installment_contract"),
            {"x_mm": -1.0, "y_mm": 2.5},
        )

    def test_missing_file_loads_defaults(self):
        loaded = PrintConfig.load(Path(self.tmp.name) / "nope.json")
        self.assertEqual(
            loaded.total_copies_for(
                "retail_installment_contract", DOC_CLASS_LAW_CONTRACT
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
