"""Stage 5, Part A: the all-letter simplification (decision 12).

Every document -- the LAW retail installment contract included -- is
standard 8.5x11 letter now, because of the migration to digital
contracting. These tests pin the new behavior end to end:

* the pack carries no long-stock document class;
* setup completes with ONLY a standard-letter tray assignment, and
  its preflight attestation is the letter test print.

(The contract printing with no stock confirmation is covered in
tests/test_print_pipeline.py, TestNoStockGate.)

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dealerpaperwork.models import DealInputs, TradeIn, Vehicle
from dealerpaperwork.pack import DOC_CLASS_STANDARD_LETTER, build_pack
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.setup_wizard import (
    DOC_STANDARD_LETTER,
    SetupStep,
    SetupWizard,
    TrayAssignment,
)
from test_setup_wizard import ok_network, sample_profile

D = Decimal


def deal_inputs(is_new: bool = True) -> DealInputs:
    return DealInputs(
        vehicle=Vehicle(
            year=2026, make="Sample", model="Wagon",
            vin="4S4BTANC0T3000001", is_new=is_new,
        ),
        selling_price=D("35000"),
        dealer_fee=D("1199"),
        county_surtax_rate=D("0.015"),
        trade=TradeIn(description="Sample trade", allowance=D("10000")),
    )


class TestPackAllLetter(unittest.TestCase):
    def test_every_pack_document_is_standard_letter(self):
        for is_new in (True, False):
            pack = build_pack(deal_inputs(is_new))
            self.assertTrue(pack)
            for doc in pack:
                self.assertEqual(doc.doc_class, DOC_CLASS_STANDARD_LETTER)

    def test_no_long_stock_class_anywhere(self):
        pack = build_pack(deal_inputs())
        classes = {doc.doc_class for doc in pack}
        self.assertNotIn("law_contract", classes)
        self.assertNotIn("law_contract_long", classes)


class TestSetupLetterOnly(unittest.TestCase):
    def test_setup_completes_with_only_a_letter_tray(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup_path = Path(tmp) / "setup.json"
            wizard = SetupWizard(
                printer_provider=FakePrinterProvider(["Printer A"]),
                setup_path=setup_path,
                network_checker=ok_network,
            )
            wizard.run_network_check()
            self.assertEqual(wizard.advance(), [])
            wizard.set_profile(sample_profile())
            self.assertEqual(wizard.advance(), [])
            wizard.select_printer("Printer A")
            self.assertEqual(wizard.advance(), [])
            # ONE assignment -- the letter tray -- is all the step asks.
            wizard.set_tray_assignment(
                TrayAssignment(
                    DOC_STANDARD_LETTER, "Printer A", "Tray 1", "Letter"
                )
            )
            self.assertEqual(wizard.advance(), [])
            self.assertEqual(wizard.current_step, SetupStep.PREFLIGHT)
            # Letter preflight: paper loaded + test print + attestation.
            wizard.confirm_paper_loaded(True)
            wizard.run_test_print()
            wizard.attest("Pat Manager", "F&I Manager")
            self.assertEqual(wizard.advance(), [])
            from dealerpaperwork.state_rules import FLORIDA
            wizard.set_state_rules(FLORIDA)
            self.assertEqual(wizard.advance(), [])
            wizard.set_tax_data(county_surtax_rates={"Sample County": D("0.015")})
            wizard.set_store_leases(False)
            self.assertEqual(wizard.advance(), [])
            from test_math_check import retail_entry
            wizard.set_retail_check(retail_entry())
            self.assertEqual(wizard.advance(), [])
            wizard.approve("Sample Manager")
            wizard.finish()
            data = json.loads(setup_path.read_text(encoding="utf-8"))
            self.assertEqual(list(data["tray_mapping"]), ["standard_letter"])
            attestation = data["attestation"]
            self.assertTrue(attestation["paper_loaded_confirmed"])
            self.assertTrue(attestation["test_print_completed"])


if __name__ == "__main__":
    unittest.main()
