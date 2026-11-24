"""Tests for the Stage 6 lease walk-through (lease_flow.LeaseDealSession).

Headless: the Stage 2 setup gate is satisfied with a written setup
file embedding the repo's SAMPLE preset plus a SAMPLE lease section
(test data only); users come from a temp UserStore; lease deals
autosave to a temp directory.

The money fixture is synthetic lease worksheet B (see
test_lease_engine.py): it must come through the whole flow --
walk-through, worksheet, certification -- at exactly the
worksheet's numbers.

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

from dealerpaperwork.audit import (
    EVENT_CERTIFICATION_INVALIDATED,
    EVENT_DEAL_CERTIFIED,
    AuditLog,
)
from dealerpaperwork.deal_flow import (
    DealPermissionError,
    SetupRequiredError,
    preset_from_dict,
    preset_from_setup_file,
)
from dealerpaperwork.lease_flow import (
    LeaseDealSession,
    LeasePresetError,
    LeaseStep,
)
from dealerpaperwork.models import Buyer, Vehicle
from dealerpaperwork.presets import UnapprovedPresetError
from dealerpaperwork.users import UserStore

D = Decimal
REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_LEASE_SECTION = {
    "lessor_name": "Sample Lessor (SAMPLE)",
    "title_holder": "Sample Lessor (SAMPLE)",
    "acquisition_fee": "650",
    "cap_fees": "60",
    "upfront_charges": "400.00",
}


def sample_preset_data(*, with_lease: bool = True) -> dict:
    sample = json.loads(
        (REPO_ROOT / "config" / "sample_preset.json").read_text(encoding="utf-8")
    )
    sample.pop("_comment", None)
    if with_lease:
        sample["lease"] = dict(SAMPLE_LEASE_SECTION)
    return sample


def write_setup_file(directory: Path, *, with_lease: bool = True) -> Path:
    setup = {
        "state": "complete",
        "completed": True,
        "completed_at": "2026-01-01T10:00:00",
        "preset": sample_preset_data(with_lease=with_lease),
    }
    path = Path(directory) / "setup.json"
    path.write_text(json.dumps(setup), encoding="utf-8")
    return path


class LeaseFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.setup_path = write_setup_file(base)
        self.audit = AuditLog(base / "audit.jsonl")
        self.store = UserStore(base / "users.json", audit_log=self.audit)
        self.admin = self.store.create_user(
            actor=None, username="ada", display_name="Ada Admin",
            role="admin", secret="admin-pin",
        )
        self.fi = self.store.create_user(
            actor=self.admin, username="fay", display_name="Fay F&I",
            role="fi", secret="fi-pin",
        )
        self.sales = self.store.create_user(
            actor=self.admin, username="sal", display_name="Sal Sales",
            role="sales", secret="sales-pin",
        )
        self.preset = preset_from_setup_file(self.setup_path)
        self.deals_dir = base / "deals"

    # -- driving helpers ---------------------------------------------------

    def new_session(self, user=None) -> LeaseDealSession:
        return LeaseDealSession.start(
            user or self.fi,
            setup_path=self.setup_path,
            deals_dir=self.deals_dir,
            audit_log=self.audit,
        )

    def walk_fixture_b_to_review(self, session) -> LeaseDealSession:
        """Drive synthetic worksheet-B lease to the review step.

        The preset seeds the predelivery fee (dealer fee $1,199),
        acquisition fee, cap fees, and upfront charges; the walk
        enters the rest."""
        session.set_buyer(
            Buyer(full_name="Sample Buyer", residence_county="Sample County")
        )
        self.assertEqual(session.advance(), [])
        session.set_vehicle(
            Vehicle(
                year=2026, make="Sample", model="Vehicle",
                vin="1SAMPLEVIN0000000", is_new=True,
            )
        )
        self.assertEqual(session.advance(), [])
        self.assertEqual(session.current_step, LeaseStep.WORKSHEET)
        session.set_lease(
            msrp="30000", dealer_discount="1500",
            residual_base_msrp="30000", residual_percent="0.62",
            term_months=36, money_factor="0.00150",
        )
        self.assertEqual(session.advance(), [])  # computes -> review
        self.assertEqual(session.current_step, LeaseStep.REVIEW)
        return session


class TestGates(LeaseFlowTestCase):
    def test_setup_gate_blocks_lease_start(self):
        missing = Path(self.tmp.name) / "no-setup.json"
        with self.assertRaises(SetupRequiredError):
            LeaseDealSession.start(
                self.fi, setup_path=missing,
                deals_dir=self.deals_dir, audit_log=self.audit,
            )

    def test_unapproved_preset_cannot_run_a_lease(self):
        data = sample_preset_data()
        data["approved_by"] = None
        data["approved_at"] = None
        with self.assertRaises(UnapprovedPresetError):
            LeaseDealSession(
                user=self.fi, preset=preset_from_dict(data),
                setup_path=self.setup_path, deals_dir=self.deals_dir,
                audit_log=self.audit,
            )

    def test_preset_without_lease_section_cannot_run_a_lease(self):
        nolease_dir = Path(self.tmp.name) / "nolease"
        nolease_dir.mkdir()
        path = write_setup_file(nolease_dir, with_lease=False)
        preset = preset_from_setup_file(path)
        self.assertIsNone(preset.lease)
        with self.assertRaises(LeasePresetError):
            LeaseDealSession(
                user=self.fi, preset=preset, setup_path=self.setup_path,
                deals_dir=self.deals_dir, audit_log=self.audit,
            )

    def test_lease_section_with_blank_lessor_is_rejected(self):
        data = sample_preset_data()
        data["lease"]["lessor_name"] = "  "
        with self.assertRaises(LeasePresetError):
            LeaseDealSession(
                user=self.fi, preset=preset_from_dict(data),
                setup_path=self.setup_path, deals_dir=self.deals_dir,
                audit_log=self.audit,
            )


class TestWalkAndCertify(LeaseFlowTestCase):
    def test_preset_defaults_seed_the_worksheet(self):
        session = self.new_session()
        self.assertEqual(session.lease.acquisition_fee, D("650"))
        self.assertEqual(session.lease.cap_fees, D("60"))
        self.assertEqual(session.lease.upfront_charges, D("400.00"))
        self.assertEqual(session.lease.predelivery_fee, D("1199.00"))

    def test_fixture_b_numbers_through_the_flow(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        result = session.worksheet_result
        self.assertIsNotNone(result)
        self.assertEqual(result.gross_cap_cost, D("30409.00"))
        self.assertEqual(result.net_cap_cost, D("30409.00"))
        self.assertEqual(result.residual_amount, D("18600.00"))
        self.assertEqual(result.base_monthly_payment, D("401.54"))
        # Sample County surtax is 1.5%, so the flow's combined rate
        # is 7.5% -- NOT the fixture's 7%. The fixture's
        # engine-level tax (28.11 at 7%) is asserted in
        # test_lease_engine.py; here the county drives the rate.
        self.assertEqual(result.combined_tax_rate, D("0.075"))
        self.assertEqual(result.monthly_tax, D("30.12"))
        self.assertEqual(result.monthly_payment, D("431.66"))

    def test_certified_lease_is_ready_to_print(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        record = session.certify()
        self.assertTrue(session.is_certified)
        self.assertTrue(session.is_printable_clean)
        self.assertIsNone(session.watermark)
        self.assertEqual(session.state, "ready_to_print")
        self.assertEqual(record.certifier_username, "fay")
        self.assertEqual(record.monthly_payment, D("431.66"))
        self.assertEqual(record.due_on_delivery, D("831.66"))
        self.assertEqual(record.lessor_name, "Sample Lessor (SAMPLE)")
        types = [e.event_type for e in self.audit.read_events()]
        self.assertIn(EVENT_DEAL_CERTIFIED, types)

    def test_uncertified_lease_is_a_draft(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        self.assertFalse(session.is_certified)
        self.assertFalse(session.is_printable_clean)
        self.assertEqual(session.watermark, "DRAFT")
        saved = json.loads(session.file_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["deal_kind"], "lease")
        self.assertEqual(saved["watermark"], "DRAFT")
        self.assertEqual(saved["state"], "review")

    def test_sales_can_edit_but_never_certify(self):
        session = self.new_session(user=self.sales)
        self.walk_fixture_b_to_review(session)
        with self.assertRaises(DealPermissionError):
            session.certify()
        self.assertFalse(session.is_certified)

    def test_edit_after_certification_invalidates_it(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.certify()
        session.set_lease(msrp="31000")
        self.assertIsNone(session.certification)
        self.assertFalse(session.is_printable_clean)
        self.assertEqual(session.current_step, LeaseStep.WORKSHEET)
        types = [e.event_type for e in self.audit.read_events()]
        self.assertIn(EVENT_CERTIFICATION_INVALIDATED, types)


class TestAutosaveResume(LeaseFlowTestCase):
    def test_resume_preserves_certification_and_numbers(self):
        session = self.walk_fixture_b_to_review(self.new_session())
        session.certify()
        deal_id = session.deal_id
        loaded = LeaseDealSession.load(
            deal_id, user=self.fi, preset=self.preset,
            deals_dir=self.deals_dir, audit_log=self.audit,
        )
        self.assertTrue(loaded.is_certified)
        self.assertTrue(loaded.is_printable_clean)
        self.assertEqual(loaded.current_step, LeaseStep.CERTIFIED)
        self.assertEqual(loaded.worksheet_result.monthly_payment, D("431.66"))
        self.assertEqual(loaded.certification.monthly_payment, D("431.66"))
        self.assertEqual(loaded.lease.msrp, D("30000"))

    def test_resume_mid_walk_keeps_step_and_inputs(self):
        session = self.new_session()
        session.set_buyer(
            Buyer(full_name="Sample Buyer", residence_county="Sample County")
        )
        session.advance()
        loaded = LeaseDealSession.load(
            session.deal_id, user=self.fi, preset=self.preset,
            deals_dir=self.deals_dir, audit_log=self.audit,
        )
        self.assertEqual(loaded.current_step, LeaseStep.VEHICLE)
        self.assertEqual(loaded.lease.buyer.full_name, "Sample Buyer")
        self.assertFalse(loaded.is_certified)


if __name__ == "__main__":
    unittest.main()
