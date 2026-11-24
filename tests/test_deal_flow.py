"""Tests for the Stage 3 deal walk-through (deal_flow.DealSession).

Headless: the Stage 2 setup gate is satisfied with a written setup
file embedding the repo's SAMPLE preset (test data only); users come
from a temp UserStore; deals autosave to a temp directory.

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
    EVENT_DEAL_CREATED,
    AuditLog,
)
from dealerpaperwork.deal_flow import (
    CertificationError,
    DealPermissionError,
    DealSession,
    DealStep,
    SetupRequiredError,
    preset_from_setup_file,
)
from dealerpaperwork.models import Buyer, TradeIn, Vehicle
from dealerpaperwork.users import UserStore

D = Decimal
REPO_ROOT = Path(__file__).resolve().parents[1]


def write_setup_file(directory: Path) -> Path:
    """A completed-setup file whose embedded preset is the repo's
    SAMPLE preset (clearly sample data; approved by 'Sample Manager
    (SAMPLE)', county table includes Sample County at 1.5%)."""
    sample = json.loads(
        (REPO_ROOT / "config" / "sample_preset.json").read_text(encoding="utf-8")
    )
    sample.pop("_comment", None)
    setup = {
        "state": "complete",
        "completed": True,
        "completed_at": "2026-01-01T10:00:00",
        "preset": sample,
    }
    path = Path(directory) / "setup.json"
    path.write_text(json.dumps(setup), encoding="utf-8")
    return path


class DealFlowTestCase(unittest.TestCase):
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
        self.manager = self.store.create_user(
            actor=self.admin, username="manny", display_name="Manny Manager",
            role="manager", secret="manager-pin",
        )
        self.sales = self.store.create_user(
            actor=self.admin, username="sal", display_name="Sal Sales",
            role="sales", secret="sales-pin",
        )
        self.preset = preset_from_setup_file(self.setup_path)
        self.deals_dir = base / "deals"

    # -- driving helpers ---------------------------------------------------

    def new_session(self, user=None) -> DealSession:
        return DealSession.start(
            user or self.fi,
            setup_path=self.setup_path,
            deals_dir=self.deals_dir,
            audit_log=self.audit,
        )

    def walk_buyer_vehicle(self, session):
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
        self.assertEqual(session.current_step, DealStep.TRADE)

    def walk_to_review(self, session, *, trade=None, money=None):
        """Drive buyer -> vehicle -> trade -> worksheet -> review."""
        self.walk_buyer_vehicle(session)
        session.set_trade(trade)
        if money:
            session.set_money(**money)
        self.assertEqual(session.advance(), [])  # -> worksheet
        self.assertEqual(session.current_step, DealStep.WORKSHEET)
        self.assertEqual(session.advance(), [])  # computes -> review
        self.assertEqual(session.current_step, DealStep.REVIEW)

    def walk_fixture_to_review(self, session):
        """The Section 9 fixture: $35,000 price, preset dealer fee
        $1,199, $10,000 trade allowance, Sample County 1.5%."""
        self.walk_to_review(
            session,
            trade=TradeIn(description="Sample trade", allowance=D("10000")),
            money={"selling_price": D("35000")},
        )

    def audit_types(self):
        return [e.event_type for e in self.audit.read_events()]


class TestSetupGate(DealFlowTestCase):
    def test_deal_cannot_start_when_setup_file_is_missing(self):
        missing = Path(self.tmp.name) / "no_such_setup.json"
        with self.assertRaises(SetupRequiredError):
            DealSession(
                user=self.fi, preset=self.preset,
                setup_path=missing, deals_dir=self.deals_dir,
            )
        with self.assertRaises(SetupRequiredError):
            DealSession.start(
                self.fi, setup_path=missing, deals_dir=self.deals_dir
            )

    def test_deal_cannot_start_when_setup_is_incomplete(self):
        incomplete = Path(self.tmp.name) / "incomplete_setup.json"
        incomplete.write_text(
            json.dumps({"state": "incomplete", "completed": False}),
            encoding="utf-8",
        )
        with self.assertRaises(SetupRequiredError):
            DealSession(
                user=self.fi, preset=self.preset,
                setup_path=incomplete, deals_dir=self.deals_dir,
            )


class TestHappyPath(DealFlowTestCase):
    def test_full_walk_certifies_and_reproduces_the_fixture(self):
        session = self.new_session()
        self.assertEqual(session.state, "buyer")
        self.assertFalse(session.is_printable_clean)
        self.assertEqual(session.watermark, "DRAFT")

        self.walk_fixture_to_review(session)
        result = session.worksheet_result
        # The Section 9 fixture amounts, with the preset's $199
        # electronic tag fee taxed under Decision 14: base =
        # 35000 + 1199 + 199 - 10000 = 26398; state tax 1583.88;
        # surtax 75.00; total tax 1658.88.
        self.assertEqual(result.taxable_base, D("26398.00"))
        self.assertEqual(result.state_tax, D("1583.88"))
        self.assertEqual(result.county_surtax, D("75.00"))
        self.assertEqual(result.total_tax, D("1658.88"))
        # Financing: 35000 + 1199 dealer fee + 1658.88 tax + 199 tag
        # fee + 0 other = 38056.88 subtotal; minus net trade equity
        # 10000 -> amount financed 28056.88. (Until 2026-11-18 the
        # flow left the taxed dealer fee out and expected 26857.88:
        # the buyer owes the fee, and the DMS worksheet finances it.)
        self.assertEqual(
            session.financing.subtotal_before_credits, D("38056.88")
        )
        self.assertEqual(session.financing.amount_financed, D("28056.88"))

        record = session.certify()
        self.assertEqual(session.current_step, DealStep.CERTIFIED)
        self.assertEqual(session.state, "ready_to_print")
        self.assertTrue(session.is_printable_clean)
        self.assertIsNone(session.watermark)
        # Certifier + preset stamp (addition #8).
        self.assertEqual(record.certifier_username, "fay")
        self.assertEqual(record.certifier_name, "Fay F&I")
        self.assertEqual(record.certifier_role, "fi")
        self.assertTrue(record.certified_at)
        self.assertEqual(record.preset_version, self.preset.version)
        self.assertEqual(record.preset_approved_by, "Sample Manager (SAMPLE)")
        self.assertEqual(record.preset_approved_at, self.preset.approved_at)
        self.assertEqual(record.total_tax, D("1658.88"))
        self.assertEqual(record.amount_financed, D("28056.88"))
        # Audit trail.
        types = self.audit_types()
        self.assertIn(EVENT_DEAL_CREATED, types)
        self.assertIn(EVENT_DEAL_CERTIFIED, types)
        # Serialised deal agrees.
        saved = json.loads(session.file_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], "ready_to_print")
        self.assertIsNone(saved["watermark"])
        self.assertEqual(
            saved["certification"]["certifier_username"], "fay"
        )

    def test_advance_is_blocked_until_buyer_is_entered(self):
        session = self.new_session()
        session.set_buyer(Buyer())  # blank
        errors = session.advance()
        self.assertTrue(any("Buyer name is blank" in e for e in errors))
        self.assertEqual(session.current_step, DealStep.BUYER)


class TestRBACEnforcement(DealFlowTestCase):
    def test_sales_can_edit_but_certify_raises(self):
        session = self.new_session(user=self.sales)
        self.walk_fixture_to_review(session)
        with self.assertRaises(DealPermissionError):
            session.certify()
        self.assertFalse(session.is_certified)
        self.assertFalse(session.is_printable_clean)

    def test_sales_cannot_certify_someone_elses_deal(self):
        session = self.new_session()
        self.walk_fixture_to_review(session)
        with self.assertRaises(DealPermissionError):
            session.certify(user=self.sales)

    def test_manager_can_certify_an_fi_users_deal(self):
        session = self.new_session()
        self.walk_fixture_to_review(session)
        record = session.certify(user=self.manager)
        self.assertEqual(record.certifier_username, "manny")
        self.assertEqual(record.certifier_role, "manager")
        self.assertTrue(session.is_printable_clean)


class TestFinancingMath(DealFlowTestCase):
    """Hand-computed expectations.

    Shared inputs: price $30,000; dealer fee overridden to $1,000;
    government fees $300; electronic tag fee $199 (from the preset);
    rebate $500; cash down $2,000; Sample County surtax 1.5%.

    Tax (engine): base = 30000 + 1000 + 199 (the tag fee, taxed
    under Decision 14) - 8000 = 23199; state = 6% x 23199 = 1391.94;
    surtax = 1.5% x first 5000 = 75.00; total tax = 1466.94.

    Financing: subtotal = 30000 + 1000 dealer fee + 1466.94 + 199
    + 300 = 32965.94.
    """

    MONEY = {
        "selling_price": D("30000"),
        "dealer_fee": D("1000"),
        "government_fees": D("300"),
        "manufacturer_rebate": D("500"),
        "cash_down_payment": D("2000"),
    }

    def build(self, *, allowance, payoff):
        session = self.new_session()
        self.walk_to_review(
            session,
            trade=TradeIn(
                description="Trade", allowance=D(allowance), payoff=D(payoff)
            ),
            money=dict(self.MONEY),
        )
        return session

    def test_financing_worked_example(self):
        # Net equity = 8000 - 3000 = 5000.
        # Amount financed = 32965.94 - 500 - 2000 - 5000 = 25465.94.
        session = self.build(allowance="8000", payoff="3000")
        self.assertEqual(session.worksheet_result.total_tax, D("1466.94"))
        financing = session.financing
        self.assertEqual(financing.subtotal_before_credits, D("32965.94"))
        self.assertEqual(financing.net_trade_equity, D("5000.00"))
        self.assertEqual(financing.amount_financed, D("25465.94"))

    def test_negative_equity_raises_amount_financed(self):
        # Payoff 9500 > allowance 8000: net equity = -1500.
        # Amount financed = 32965.94 - 500 - 2000 - (-1500) = 31965.94.
        positive = self.build(allowance="8000", payoff="3000")
        negative = self.build(allowance="8000", payoff="9500")
        self.assertEqual(
            negative.financing.net_trade_equity, D("-1500.00")
        )
        self.assertEqual(negative.financing.amount_financed, D("31965.94"))
        # The financed amount rises by exactly the extra payoff.
        self.assertEqual(
            negative.financing.amount_financed
            - positive.financing.amount_financed,
            D("6500.00"),
        )

    def test_cash_down_payment_is_a_session_input(self):
        session = self.new_session()
        self.assertEqual(session.cash_down_payment, D("0"))
        session.cash_down_payment = D("1500")
        self.assertEqual(session.inputs.cash_down_payment, D("1500"))


class TestCertificationInvalidation(DealFlowTestCase):
    def test_money_edit_after_certify_invalidates(self):
        session = self.new_session()
        self.walk_fixture_to_review(session)
        session.certify()
        self.assertTrue(session.is_printable_clean)

        session.set_money(selling_price=D("36000"))
        self.assertIsNone(session.certification)
        self.assertFalse(session.is_printable_clean)
        self.assertEqual(session.watermark, "DRAFT")
        self.assertEqual(session.current_step, DealStep.WORKSHEET)
        self.assertEqual(session.state, "worksheet")
        self.assertIn(EVENT_CERTIFICATION_INVALIDATED, self.audit_types())

    def test_buyer_edit_after_certify_invalidates(self):
        session = self.new_session()
        self.walk_fixture_to_review(session)
        session.certify()
        session.set_buyer(
            Buyer(full_name="Different Buyer", residence_county="Sample County")
        )
        self.assertIsNone(session.certification)
        self.assertFalse(session.is_printable_clean)
        self.assertIn(EVENT_CERTIFICATION_INVALIDATED, self.audit_types())


class TestSubFiveThousandWarning(DealFlowTestCase):
    """Base below $5,000: price 12000 + dealer fee 1000 + the preset's
    $199 tag fee (taxed, Decision 14) - trade 9000 = 4199. State tax
    251.94, surtax 1.5% x 4199 = 62.99, total 314.93.
    The engine flags this case (spec Section 16); certification must
    wait for the certifier's own acknowledgment."""

    def build_session(self):
        session = self.new_session()
        self.walk_to_review(
            session,
            trade=TradeIn(description="Trade", allowance=D("9000")),
            money={"selling_price": D("12000"), "dealer_fee": D("1000")},
        )
        return session

    def test_certification_blocked_until_certifier_acknowledges(self):
        session = self.build_session()
        self.assertEqual(session.worksheet_result.taxable_base, D("4199.00"))
        self.assertEqual(session.worksheet_result.total_tax, D("314.93"))
        self.assertTrue(session.warnings)

        with self.assertRaises(CertificationError):
            session.certify()
        # Someone else's acknowledgment does not unblock the certifier.
        session.acknowledge_warnings(user=self.sales)
        with self.assertRaises(CertificationError):
            session.certify()
        # The certifying user's own acknowledgment does.
        session.acknowledge_warnings(user=self.fi)
        self.assertEqual(session.warnings_acknowledged_by, "fay")
        self.assertTrue(session.warnings_acknowledged_at)
        record = session.certify()
        self.assertEqual(record.total_tax, D("314.93"))
        self.assertTrue(session.is_printable_clean)
        saved = json.loads(session.file_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["warnings_acknowledged_by"], "fay")


class TestAutosaveResume(DealFlowTestCase):
    def test_mid_flow_save_and_resume_restores_state_exactly(self):
        session = self.new_session()
        self.walk_buyer_vehicle(session)
        session.set_money(selling_price=D("35000"))
        # Autosaved after every change: the file already exists.
        self.assertTrue(session.file_path.exists())

        loaded = DealSession.load(
            session.deal_id,
            user=self.fi,
            preset=self.preset,
            deals_dir=self.deals_dir,
            audit_log=self.audit,
        )
        self.assertEqual(loaded.current_step, DealStep.TRADE)
        self.assertEqual(loaded.inputs.buyer.full_name, "Sample Buyer")
        self.assertEqual(loaded.inputs.buyer.residence_county, "Sample County")
        self.assertEqual(loaded.inputs.vehicle.vin, "1SAMPLEVIN0000000")
        self.assertEqual(loaded.inputs.selling_price, D("35000"))
        self.assertEqual(loaded.inputs.dealer_fee, D("1199"))  # preset copy
        self.assertFalse(loaded.is_certified)

    def test_certified_deal_resumes_as_certified(self):
        session = self.new_session()
        self.walk_fixture_to_review(session)
        session.certify()

        loaded = DealSession.load(
            session.deal_id,
            user=self.fi,
            preset=self.preset,
            deals_dir=self.deals_dir,
            audit_log=self.audit,
        )
        self.assertTrue(loaded.is_certified)
        self.assertEqual(loaded.state, "ready_to_print")
        self.assertTrue(loaded.is_printable_clean)
        self.assertIsNone(loaded.watermark)
        self.assertEqual(loaded.certification.certifier_username, "fay")
        self.assertEqual(loaded.certification.total_tax, D("1658.88"))
        self.assertEqual(
            loaded.worksheet_result.total_tax, D("1658.88")
        )
        self.assertEqual(
            loaded.financing.amount_financed, D("28056.88")
        )


if __name__ == "__main__":
    unittest.main()
