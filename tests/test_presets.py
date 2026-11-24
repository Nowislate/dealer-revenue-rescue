"""Tests for presets: dealer-neutral defaults, validation, approval gate.

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

from dealerpaperwork.models import DealerProfile
from dealerpaperwork.presets import (
    Preset,
    UnapprovedPresetError,
    load_preset,
    require_approved,
    validate_preset,
)
from dealerpaperwork.tax_engine import calculate_retail_deal
from test_tax_engine import fixture_inputs

REPO_ROOT = Path(__file__).resolve().parents[1]


def complete_preset(**kwargs) -> Preset:
    """A complete, valid preset with SAMPLE dealer values."""
    dealer = DealerProfile(
        legal_name="Sample Motors LLC (SAMPLE)",
        dealer_fee=Decimal("1199"),
        electronic_tag_fee=Decimal("199"),
        disclosure_501_976_18_confirmed=True,
    )
    return Preset(
        dealer=dealer,
        county_surtax_rates={"Sample County": Decimal("0.015")},
        **kwargs,
    )


class TestDealerNeutral(unittest.TestCase):
    def test_fresh_dealer_profile_has_no_identity_or_fees(self):
        profile = DealerProfile()
        self.assertTrue(profile.is_blank)
        self.assertIsNone(profile.legal_name)
        self.assertIsNone(profile.trading_name)
        self.assertIsNone(profile.street_address)
        self.assertIsNone(profile.licence_number)
        self.assertIsNone(profile.title_filing_method)
        self.assertIsNone(profile.dealer_fee)
        self.assertIsNone(profile.electronic_tag_fee)

    def test_fresh_preset_is_unapproved_and_invalid(self):
        preset = Preset()
        self.assertFalse(preset.is_approved)
        self.assertTrue(validate_preset(preset))  # errors listed


class TestApprovalGate(unittest.TestCase):
    def test_unapproved_preset_cannot_certify(self):
        preset = complete_preset()  # complete but NOT approved
        self.assertEqual(validate_preset(preset), [])
        with self.assertRaises(UnapprovedPresetError):
            require_approved(preset)
        with self.assertRaises(UnapprovedPresetError):
            calculate_retail_deal(
                fixture_inputs(), preset=preset, certify=True
            )

    def test_missing_preset_cannot_certify(self):
        with self.assertRaises(UnapprovedPresetError):
            calculate_retail_deal(fixture_inputs(), certify=True)

    def test_approved_preset_can_certify_and_matches_fixture(self):
        preset = complete_preset(
            approved_by="Sample Manager", approved_at="2026-01-01T09:00:00"
        )
        require_approved(preset)  # must not raise
        result = calculate_retail_deal(
            fixture_inputs(), preset=preset, certify=True
        )
        self.assertEqual(result.total_tax, Decimal("1646.94"))

    def test_draft_calculation_does_not_require_approval(self):
        # Estimates/drafts may run without an approved preset (they can
        # only ever print as DRAFT in later stages -- spec Section 8).
        result = calculate_retail_deal(fixture_inputs(), certify=False)
        self.assertEqual(result.total_tax, Decimal("1646.94"))


class TestValidation(unittest.TestCase):
    def test_blank_fee_blocks_approval(self):
        preset = complete_preset(
            approved_by="Sample Manager", approved_at="2026-01-01T09:00:00"
        )
        preset.dealer.dealer_fee = None
        errors = validate_preset(preset)
        self.assertTrue(any("Dealer fee is blank" in e for e in errors))
        with self.assertRaises(UnapprovedPresetError):
            require_approved(preset)

    def test_unconfirmed_disclosure_is_an_error(self):
        preset = complete_preset()
        preset.dealer.disclosure_501_976_18_confirmed = False
        self.assertTrue(
            any("501.976" in e for e in validate_preset(preset))
        )


class TestLoadPreset(unittest.TestCase):
    def test_sample_preset_file_loads_and_is_approved(self):
        preset = load_preset(REPO_ROOT / "config" / "sample_preset.json")
        self.assertTrue(preset.is_approved)
        self.assertEqual(preset.dealer.dealer_fee, Decimal("1199.00"))
        self.assertEqual(
            preset.surtax_rate_for("Sample County"), Decimal("0.015")
        )
        self.assertEqual(validate_preset(preset), [])
        result = calculate_retail_deal(
            fixture_inputs(), preset=preset, certify=True
        )
        self.assertEqual(result.total_tax, Decimal("1646.94"))

    def test_load_unapproved_preset_from_json(self):
        data = {
            "dealer": {"legal_name": "Sample (SAMPLE)"},
            "county_surtax_rates": {"Sample County": "0.015"},
        }
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fh:
            json.dump(data, fh)
            path = fh.name
        preset = load_preset(path)
        self.assertFalse(preset.is_approved)
        with self.assertRaises(UnapprovedPresetError):
            require_approved(preset)

    def test_unknown_county_rate_is_not_guessed(self):
        preset = complete_preset()
        with self.assertRaises(KeyError):
            preset.surtax_rate_for("No Such County")


if __name__ == "__main__":
    unittest.main()
