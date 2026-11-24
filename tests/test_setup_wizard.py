"""Tests for the Step 0 setup wizard state machine (Stage 2).

Fully headless: FakePrinterProvider + an injected network checker.
No UI, no real printers, no network traffic.

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

from dealerpaperwork.models import DealerProfile
from dealerpaperwork.network_check import NetworkCheckResult
from dealerpaperwork.presets import load_preset, require_approved, validate_preset
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.setup_wizard import (
    DOC_STANDARD_LETTER,
    TITLE_FILING_EFS,
    SetupIncompleteError,
    SetupStep,
    SetupWizard,
    TrayAssignment,
    is_setup_complete,
)
from dealerpaperwork.tax_engine import calculate_retail_deal
from test_tax_engine import fixture_inputs

D = Decimal
PRINTERS = ["Printer A", "Printer B"]


def ok_network() -> NetworkCheckResult:
    return NetworkCheckResult(
        ok=True, local_ip="192.168.1.20", message="On the store LAN."
    )


def bad_network() -> NetworkCheckResult:
    return NetworkCheckResult(
        ok=False, local_ip=None, message="No local network address found."
    )


def sample_profile(**overrides) -> DealerProfile:
    """A complete profile with SAMPLE values, as entered in setup."""
    profile = DealerProfile(
        legal_name="Sample Motors LLC (SAMPLE)",
        trading_name="Sample Motors (SAMPLE)",
        street_address="100 Sample Street",
        city="Sample City",
        state="FL",
        zip_code="00000",
        phone="555-0100",
        contact_email="sample@example.invalid",
        licence_number="SAMPLE-LICENCE-000",
        franchise_info="Sample franchise (SAMPLE)",
        title_filing_method=TITLE_FILING_EFS,
        dealer_fee=D("1199"),
        electronic_tag_fee=D("199"),
        disclosure_501_976_18_confirmed=True,
    )
    for key, value in overrides.items():
        setattr(profile, key, value)
    return profile


class WizardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.setup_path = Path(self.tmp.name) / "setup.json"
        self.provider = FakePrinterProvider(PRINTERS)
        self.wizard = SetupWizard(
            printer_provider=self.provider,
            setup_path=self.setup_path,
            network_checker=ok_network,
        )

    # Drive helpers: each returns the advance() error list.
    def pass_network(self):
        self.wizard.run_network_check()
        return self.wizard.advance()

    def pass_profile(self, profile=None):
        self.wizard.set_profile(profile or sample_profile())
        return self.wizard.advance()

    def pass_printer(self):
        self.wizard.select_printer("Printer A")
        return self.wizard.advance()

    def pass_trays(self):
        # Stage 5: only the standard-letter assignment is required --
        # the contract is a letter document now too.
        self.wizard.set_tray_assignment(
            TrayAssignment(DOC_STANDARD_LETTER, "Printer A", "Tray 1", "Letter")
        )
        return self.wizard.advance()

    def pass_preflight(self, name="Pat Manager", role="F&I Manager"):
        self.wizard.confirm_paper_loaded(True)
        self.wizard.run_test_print()
        self.wizard.attest(name, role)
        return self.wizard.advance()

    def pass_state_rules(self, rules=None):
        # Decision 22: Florida's built-in sheet unless a test types one.
        from dealerpaperwork.state_rules import FLORIDA
        self.assertEqual(self.wizard.current_step, SetupStep.STATE_RULES)
        self.wizard.set_state_rules(rules or FLORIDA)
        return self.wizard.advance()

    def pass_tax_rates(self):
        if self.wizard.current_step is SetupStep.STATE_RULES:
            self.assertEqual(self.pass_state_rules(), [])
        self.wizard.set_tax_data(
            county_surtax_rates={"Sample County": D("0.015")}
        )
        self.wizard.set_store_leases(False)
        return self.wizard.advance()

    def pass_math_check(self):
        # Decision 21: the synthetic retail deal of test_math_check,
        # whose DMS figures the app reproduces to the cent.
        from test_math_check import retail_entry
        self.wizard.set_retail_check(retail_entry())
        return self.wizard.advance()

    def drive_to_approve(self):
        self.assertEqual(self.pass_network(), [])
        self.assertEqual(self.pass_profile(), [])
        self.assertEqual(self.pass_printer(), [])
        self.assertEqual(self.pass_trays(), [])
        self.assertEqual(self.pass_preflight(), [])
        self.assertEqual(self.pass_tax_rates(), [])
        self.assertEqual(self.pass_math_check(), [])
        self.assertEqual(self.wizard.current_step, SetupStep.APPROVE)

    def finish_happy_path(self):
        self.drive_to_approve()
        self.wizard.approve("Sample Manager")
        return self.wizard.finish()


class TestHappyPath(WizardTestCase):
    def test_full_setup_completes_and_persists(self):
        self.assertFalse(is_setup_complete(self.setup_path))
        record = self.finish_happy_path()
        self.assertTrue(record.completed)
        self.assertEqual(self.wizard.current_step, SetupStep.DONE)
        self.assertTrue(self.setup_path.exists())
        self.assertTrue(is_setup_complete(self.setup_path))

    def test_test_print_went_to_the_assigned_printer(self):
        self.finish_happy_path()
        self.assertEqual(self.provider.test_prints, ["Printer A"])

    def test_attestation_recorded_with_name_role_timestamp(self):
        self.finish_happy_path()
        data = json.loads(self.setup_path.read_text(encoding="utf-8"))
        attestation = data["attestation"]
        self.assertEqual(attestation["attester_name"], "Pat Manager")
        self.assertEqual(attestation["attester_role"], "F&I Manager")
        self.assertTrue(attestation["attested_at"])
        self.assertTrue(attestation["paper_loaded_confirmed"])
        self.assertTrue(attestation["test_print_completed"])

    def test_setup_file_round_trip(self):
        record = self.finish_happy_path()
        data = json.loads(self.setup_path.read_text(encoding="utf-8"))
        self.assertEqual(data["dealer"]["legal_name"], "Sample Motors LLC (SAMPLE)")
        self.assertEqual(data["dealer"]["dealer_fee"], "1199")
        self.assertEqual(data["dealer"]["electronic_tag_fee"], "199")
        self.assertEqual(data["selected_printer"], "Printer A")
        letter = data["tray_mapping"][DOC_STANDARD_LETTER]
        self.assertEqual(letter["tray"], "Tray 1")
        self.assertEqual(letter["paper"], "Letter")
        # Stage 4 calibration slot is present from the start.
        self.assertEqual(letter["alignment_offsets_mm"], {})
        self.assertEqual(record.dealer.legal_name, "Sample Motors LLC (SAMPLE)")

    def test_approved_preset_from_setup_certifies_the_fixture(self):
        self.finish_happy_path()
        data = json.loads(self.setup_path.read_text(encoding="utf-8"))
        preset_path = Path(self.tmp.name) / "preset_from_setup.json"
        preset_path.write_text(json.dumps(data["preset"]), encoding="utf-8")
        preset = load_preset(preset_path)
        self.assertEqual(validate_preset(preset), [])
        require_approved(preset)  # must not raise
        result = calculate_retail_deal(
            fixture_inputs(), preset=preset, certify=True
        )
        self.assertEqual(result.taxable_base, D("26199.00"))
        self.assertEqual(result.state_tax, D("1571.94"))
        self.assertEqual(result.county_surtax, D("75.00"))
        self.assertEqual(result.total_tax, D("1646.94"))


class TestGating(WizardTestCase):
    def test_network_failure_blocks_advance(self):
        wizard = SetupWizard(
            printer_provider=self.provider,
            setup_path=self.setup_path,
            network_checker=bad_network,
        )
        wizard.run_network_check()
        errors = wizard.advance()
        self.assertTrue(errors)
        self.assertEqual(wizard.current_step, SetupStep.NETWORK)

    def test_network_step_requires_running_the_check(self):
        errors = self.wizard.advance()
        self.assertTrue(any("network check" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.NETWORK)

    def test_blank_dealer_name_blocks_profile_step(self):
        self.pass_network()
        errors = self.pass_profile(sample_profile(legal_name=""))
        self.assertTrue(any("legal name is blank" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.PROFILE)

    def test_blank_fees_block_profile_step(self):
        self.pass_network()
        errors = self.pass_profile(
            sample_profile(dealer_fee=None, electronic_tag_fee=None)
        )
        self.assertTrue(any("Dealer fee is blank" in e for e in errors))
        self.assertTrue(any("Electronic tag fee is blank" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.PROFILE)

    def test_missing_title_filing_method_blocks_profile_step(self):
        self.pass_network()
        errors = self.pass_profile(sample_profile(title_filing_method=None))
        self.assertTrue(any("files titles" in e for e in errors))

    def test_printer_step_requires_a_selection(self):
        self.pass_network()
        self.pass_profile()
        errors = self.wizard.advance()  # nothing selected
        self.assertTrue(any("Select a printer" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.PRINTER)

    def test_printer_step_rejects_unknown_printer(self):
        self.pass_network()
        self.pass_profile()
        self.wizard.select_printer("Ghost Printer")
        errors = self.wizard.advance()
        self.assertTrue(any("not in the list" in e for e in errors))

    def test_tray_step_requires_the_letter_assignment(self):
        # Stage 5: only ONE assignment is required (all documents are
        # letter); before, both a law-contract and a letter assignment
        # were demanded.
        self.pass_network()
        self.pass_profile()
        self.pass_printer()
        errors = self.wizard.advance()  # no assignments at all
        self.assertTrue(any("standard letter" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.TRAY_MAPPING)
        # The single letter assignment alone passes the step.
        self.assertEqual(self.pass_trays(), [])
        self.assertEqual(self.wizard.current_step, SetupStep.PREFLIGHT)

    def test_tray_step_rejects_non_letter_paper(self):
        # Stage 5: letter paper is right for EVERY document now; a
        # leftover "long stock" paper name is what gets rejected.
        self.pass_network()
        self.pass_profile()
        self.pass_printer()
        self.wizard.set_tray_assignment(
            TrayAssignment(DOC_STANDARD_LETTER, "Printer A", "Tray 1", "Long contract stock")
        )
        errors = self.wizard.advance()
        self.assertTrue(any("letter" in e for e in errors))

    def test_preflight_requires_attestation(self):
        self.pass_network()
        self.pass_profile()
        self.pass_printer()
        self.pass_trays()
        self.wizard.confirm_paper_loaded(True)
        self.wizard.run_test_print()
        errors = self.wizard.advance()  # nobody attested
        self.assertTrue(any("attestation is required" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.PREFLIGHT)

    def test_preflight_blank_attester_name_fails(self):
        self.pass_network()
        self.pass_profile()
        self.pass_printer()
        self.pass_trays()
        errors = self.pass_preflight(name="  ", role="F&I Manager")
        self.assertTrue(any("Attester name is blank" in e for e in errors))
        self.assertEqual(self.wizard.current_step, SetupStep.PREFLIGHT)

    def test_preflight_requires_paper_and_test_print(self):
        self.pass_network()
        self.pass_profile()
        self.pass_printer()
        self.pass_trays()
        self.wizard.attest("Pat Manager", "F&I Manager")
        errors = self.wizard.advance()
        self.assertTrue(any("letter paper" in e for e in errors))
        self.assertTrue(any("test print" in e for e in errors))

    def test_finish_raises_until_everything_is_done(self):
        with self.assertRaises(SetupIncompleteError):
            self.wizard.finish()
        self.pass_network()
        with self.assertRaises(SetupIncompleteError):
            self.wizard.finish()
        self.assertFalse(is_setup_complete(self.setup_path))

    def test_finish_requires_preset_data_to_validate(self):
        # Everything done except the county surtax table: Stage 1's
        # preset validation must still block completion.
        self.drive_to_approve()
        self.wizard.set_tax_data(county_surtax_rates={})
        self.wizard.approve("Sample Manager")
        with self.assertRaises(SetupIncompleteError) as ctx:
            self.wizard.finish()
        self.assertIn("DR-15DSS", str(ctx.exception))

    def test_back_navigation(self):
        self.pass_network()
        self.assertEqual(self.wizard.current_step, SetupStep.PROFILE)
        self.wizard.back()
        self.assertEqual(self.wizard.current_step, SetupStep.NETWORK)


class TestIslandMode(WizardTestCase):
    """Decision 15: one PC with a direct-attached printer needs no LAN."""

    def setUp(self):
        super().setUp()
        self.checks = 0

        def no_network():
            self.checks += 1
            return bad_network()

        self.wizard = SetupWizard(
            printer_provider=self.provider,
            setup_path=self.setup_path,
            network_checker=no_network,
        )

    def test_no_network_blocks_until_standalone_is_chosen_by_name(self):
        self.wizard.run_network_check()
        self.assertNotEqual(self.wizard.advance(), [])
        with self.assertRaises(ValueError):
            self.wizard.choose_standalone("   ")
        self.assertNotEqual(self.wizard.advance(), [])
        self.wizard.choose_standalone("Pat Manager")
        self.assertEqual(self.wizard.advance(), [])
        self.assertEqual(self.wizard.current_step, SetupStep.PROFILE)

    def test_standalone_setup_completes_and_records_the_choice(self):
        self.wizard.choose_standalone("Pat Manager")
        self.assertEqual(self.wizard.advance(), [])
        self.assertEqual(self.pass_profile(), [])
        self.assertEqual(self.pass_printer(), [])
        self.assertEqual(self.pass_trays(), [])
        self.assertEqual(self.pass_preflight(), [])
        self.assertEqual(self.pass_tax_rates(), [])
        self.assertEqual(self.pass_math_check(), [])
        self.wizard.approve("Sample Manager")
        self.wizard.finish()
        self.assertTrue(is_setup_complete(self.setup_path))
        self.assertEqual(self.checks, 0, "standalone setup must not call the network checker")
        network = json.loads(self.setup_path.read_text(encoding="utf-8"))["network"]
        self.assertTrue(network["ok"])
        self.assertIsNone(network["local_ip"])
        self.assertIn("Standalone (island mode)", network["message"])
        self.assertIn("Pat Manager", network["message"])


class TestIsSetupComplete(unittest.TestCase):
    def test_missing_file_is_not_complete(self):
        self.assertFalse(is_setup_complete("/nonexistent/setup.json"))

    def test_garbage_file_is_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "setup.json"
            path.write_text("{ not json", encoding="utf-8")
            self.assertFalse(is_setup_complete(path))

    def test_incomplete_state_is_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "setup.json"
            path.write_text(
                json.dumps({"state": "incomplete", "completed": False}),
                encoding="utf-8",
            )
            self.assertFalse(is_setup_complete(path))


if __name__ == "__main__":
    unittest.main()
