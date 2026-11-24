"""Tests for the Stage 4 pack definition (pack.py).

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.models import DealInputs, Vehicle
from dealerpaperwork.pack import (
    DOC_CLASS_STANDARD_LETTER,
    KIND_PRINT,
    KIND_SECURE_STOCK,
    build_pack,
)


def inputs_for(is_new: bool) -> DealInputs:
    inputs = DealInputs()
    inputs.vehicle = Vehicle(year=2026, make="Sample", model="Vehicle", is_new=is_new)
    return inputs


class TestPackComposition(unittest.TestCase):
    def test_standard_pack_order_and_contract_class(self):
        # Stage 5 (decision 12): the contract is a letter document
        # like everything else -- the long-stock class is retired.
        pack = build_pack(inputs_for(is_new=True))
        ids = [doc.doc_id for doc in pack]
        self.assertEqual(ids[0], "retail_installment_contract")
        contract = pack[0]
        self.assertEqual(contract.doc_class, DOC_CLASS_STANDARD_LETTER)
        self.assertEqual(contract.kind, KIND_PRINT)
        for doc in pack:
            self.assertEqual(doc.doc_class, DOC_CLASS_STANDARD_LETTER)
        for expected in (
            "buyers_order", "deal_worksheet", "title_application",
            "odometer_statement",
        ):
            self.assertIn(expected, ids)

    def test_new_vehicle_excludes_buyers_guide(self):
        ids = [doc.doc_id for doc in build_pack(inputs_for(is_new=True))]
        self.assertNotIn("buyers_guide", ids)
        self.assertNotIn("buyers_guide_note", ids)

    def test_used_vehicle_includes_buyers_guide(self):
        ids = [doc.doc_id for doc in build_pack(inputs_for(is_new=False))]
        self.assertIn("buyers_guide", ids)
        self.assertIn("buyers_guide_note", ids)

    def test_secure_stock_entries_present_and_marked(self):
        pack = build_pack(inputs_for(is_new=True))
        secure = [doc for doc in pack if doc.kind == KIND_SECURE_STOCK]
        self.assertEqual(
            {doc.doc_id for doc in secure},
            {"secure_poa", "secure_reassignment"},
        )
        for doc in secure:
            self.assertFalse(doc.is_printable)
            self.assertTrue(doc.is_secure_stock)
            self.assertIn("SECURE STOCK", doc.title)


if __name__ == "__main__":
    unittest.main()
