"""Tests for the Stage 4 print pipeline (print_pipeline.py).

Headless: FakePrinterProvider only. Deals are real Stage 3 sessions
driven to certification on the SAMPLE preset; the setup file carries
a tray mapping onto the fake printers.

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

from dealerpaperwork.audit import AuditLog
from dealerpaperwork.deal_flow import DealSession, preset_from_setup_file
from dealerpaperwork.models import Buyer, TradeIn, Vehicle
from dealerpaperwork.print_config import CopySet, PrintConfig
from dealerpaperwork.print_pipeline import (
    STATUS_FAILED,
    STATUS_PRINTED,
    STATUS_PULLED,
    PrintPipelineError,
    PrintSession,
)
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.users import UserStore

D = Decimal
REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PRINTER = "FakePrinter - F&I (Long Stock)"
LETTER_PRINTER = "FakePrinter - Office (Letter)"
CONTRACT = "retail_installment_contract"


def tray_mapping(*, include_letter: bool = True) -> dict:
    mapping = {}
    if include_letter:
        mapping["standard_letter"] = {
            "document_class": "standard_letter",
            "printer_name": LETTER_PRINTER,
            "tray": "Tray 1",
            "paper": "Letter",
            "alignment_offsets_mm": {},
        }
    return mapping


class PrintPipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        sample = json.loads(
            (REPO_ROOT / "config" / "sample_preset.json").read_text(encoding="utf-8")
        )
        sample.pop("_comment", None)
        self.setup_data = {
            "state": "complete",
            "completed": True,
            "completed_at": "2026-01-01T10:00:00",
            "selected_printer": CONTRACT_PRINTER,
            "tray_mapping": tray_mapping(),
            "preset": sample,
        }
        self.setup_path = base / "setup.json"
        self.setup_path.write_text(json.dumps(self.setup_data), encoding="utf-8")
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
        self.preset = preset_from_setup_file(self.setup_path)
        self.deals_dir = base / "deals"
        self.sessions_dir = base / "print_sessions"
        self.provider = FakePrinterProvider()
        self.config_path = base / "print_config.json"

    # -- helpers ---------------------------------------------------------

    def make_deal(self, *, certify: bool = True, is_new: bool = True) -> DealSession:
        session = DealSession.start(
            self.fi,
            setup_path=self.setup_path,
            deals_dir=self.deals_dir,
            audit_log=self.audit,
        )
        session.set_buyer(
            Buyer(full_name="Sample Buyer", residence_county="Sample County")
        )
        session.advance()
        session.set_vehicle(
            Vehicle(
                year=2026, make="Sample", model="Vehicle",
                vin="1SAMPLEVIN0000000", is_new=is_new,
            )
        )
        session.advance()
        session.set_trade(
            TradeIn(description="Sample trade", allowance=D("10000"))
        )
        session.set_money(selling_price=D("35000"))
        session.advance()  # -> worksheet
        session.advance()  # computes -> review
        if certify:
            session.certify()
        return session

    def make_print_session(
        self, deal, *, setup=None, stock=True, provider=None, config=None
    ) -> PrintSession:
        return PrintSession.from_setup(
            deal=deal,
            setup=self.setup_path if setup is None else setup,
            print_config=config or PrintConfig(path=self.config_path),
            session_dir=self.sessions_dir,
            provider=provider or self.provider,
            audit_log=self.audit,
            actor=self.fi,
            stock_confirmation=lambda doc: stock,
        )

    def audit_types(self) -> list[str]:
        return [e.event_type for e in self.audit.read_events()]

    def finish_pack(self, ps: PrintSession) -> None:
        ps.print_all()
        ps.pull_secure_item("secure_poa")
        ps.pull_secure_item("secure_reassignment")

    def finish_packaging(self, ps: PrintSession) -> None:
        for row in ps.packaging_rows:
            ps.check_packaging_item(row.item_id, actor=self.fi,
                                   reference='SAMPLE paper original / secure form 001')


class TestPackPrinting(PrintPipelineTestCase):
    def test_secure_stock_is_never_sent_to_the_printer(self):
        deal = self.make_deal()
        ps = self.make_print_session(deal)
        self.finish_pack(ps)
        sent = {job.doc_id for job in self.provider.jobs}
        self.assertNotIn("secure_poa", sent)
        self.assertNotIn("secure_reassignment", sent)
        self.assertEqual(ps.item("secure_poa").status, STATUS_PULLED)
        self.assertEqual(ps.item("secure_poa").pulled_by, "fay")
        self.assertFalse(ps.is_complete)
        self.finish_packaging(ps)
        self.assertTrue(ps.is_complete)
        self.assertIn("secure_stock_pulled", self.audit_types())
        self.assertIn("pack_print_completed", self.audit_types())

    def test_printing_a_secure_item_directly_raises(self):
        ps = self.make_print_session(self.make_deal())
        with self.assertRaises(PrintPipelineError):
            ps.print_item("secure_poa")

    def test_certified_deal_prints_clean(self):
        ps = self.make_print_session(self.make_deal(certify=True))
        self.assertFalse(ps.draft_only)
        ps.print_all()
        self.assertTrue(self.provider.jobs)
        for job in self.provider.jobs:
            self.assertNotIn("DRAFT", job.payload)

    def test_uncertified_deal_prints_draft_everywhere(self):
        ps = self.make_print_session(self.make_deal(certify=False))
        self.assertTrue(ps.draft_only)
        ps.print_all()
        self.assertTrue(self.provider.jobs)
        for job in self.provider.jobs:
            self.assertIn("DRAFT", job.payload)


class TestTrayAndPaper(PrintPipelineTestCase):
    def test_contract_uses_the_letter_assignment_like_everything_else(self):
        # Stage 5 (decision 12): the contract is a letter document --
        # it resolves through the standard-letter assignment, exactly
        # like the buyer's order. Before Stage 5 it went to a separate
        # long-stock tray assignment.
        ps = self.make_print_session(self.make_deal())
        ps.print_all()
        contract_jobs = self.provider.jobs_for(CONTRACT)
        self.assertEqual(len(contract_jobs), 1)
        self.assertEqual(contract_jobs[0].printer_name, LETTER_PRINTER)
        self.assertEqual(contract_jobs[0].tray, "Tray 1")
        self.assertEqual(contract_jobs[0].paper, "Letter")
        order_jobs = self.provider.jobs_for("buyers_order")
        self.assertEqual(order_jobs[0].printer_name, LETTER_PRINTER)
        self.assertEqual(order_jobs[0].tray, "Tray 1")
        self.assertEqual(order_jobs[0].paper, "Letter")

    def test_missing_letter_assignment_fails_cleanly(self):
        # Stage 5 update: the failure case is now a missing LETTER
        # assignment (the old test removed the long-stock assignment).
        data = dict(self.setup_data)
        data["tray_mapping"] = tray_mapping(include_letter=False)
        path = Path(self.tmp.name) / "setup_no_letter.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        ps = self.make_print_session(self.make_deal(), setup=path)
        with self.assertRaises(PrintPipelineError):
            ps.print_item(CONTRACT)
        self.assertEqual(self.provider.jobs_for(CONTRACT), [])
        self.assertEqual(ps.item(CONTRACT).status, STATUS_FAILED)
        self.assertIn("No tray assignment", ps.item(CONTRACT).error)


class TestCopySets(PrintPipelineTestCase):
    def test_contract_and_worksheet_copy_counts(self):
        ps = self.make_print_session(self.make_deal())
        ps.print_all()
        self.assertEqual(self.provider.jobs_for(CONTRACT)[0].copies, 3)
        self.assertEqual(self.provider.jobs_for("deal_worksheet")[0].copies, 1)
        self.assertEqual(self.provider.jobs_for("buyers_order")[0].copies, 2)

    def test_changed_preset_changes_the_job(self):
        config = PrintConfig(path=self.config_path)
        config.set_copy_set(
            CopySet(buyer=2, dealer=1, lender=1), doc_id="buyers_order"
        )
        ps = self.make_print_session(self.make_deal(), config=config)
        ps.print_item("buyers_order")
        self.assertEqual(self.provider.jobs_for("buyers_order")[0].copies, 4)


class TestFailureAndResume(PrintPipelineTestCase):
    def test_failure_stops_the_run_and_resume_never_duplicates(self):
        provider = FakePrinterProvider(fail_on={"buyers_order"})
        ps = self.make_print_session(self.make_deal(), provider=provider)
        summary = ps.print_all()
        self.assertEqual(summary["failed"], "buyers_order")
        self.assertEqual(ps.item(CONTRACT).status, STATUS_PRINTED)
        self.assertEqual(ps.item("buyers_order").status, STATUS_FAILED)
        self.assertEqual(ps.item("deal_worksheet").status, "pending")
        # "Fix" the printer and resume.
        provider.fail_on.clear()
        summary2 = ps.resume()
        self.assertIsNone(summary2["failed"])
        # The contract was printed exactly once -- resume skipped it.
        self.assertEqual(len(provider.jobs_for(CONTRACT)), 1)
        self.assertEqual(ps.item("buyers_order").status, STATUS_PRINTED)
        self.assertEqual(ps.item("deal_worksheet").status, STATUS_PRINTED)
        self.assertIn("document_print_failed", self.audit_types())


class TestVoidAndReprint(PrintPipelineTestCase):
    def test_void_then_reprint_is_marked_and_audited(self):
        ps = self.make_print_session(self.make_deal())
        ps.print_item(CONTRACT)
        ps.void_item(CONTRACT, reason="Paper jam damaged the form")
        self.assertEqual(ps.item(CONTRACT).status, "pending")
        self.assertEqual(ps.item(CONTRACT).void_reason, "Paper jam damaged the form")
        ps.reprint_item(CONTRACT)
        reprint_job = self.provider.jobs_for(CONTRACT)[-1]
        self.assertIn("REPRINT -- original voided", reprint_job.payload)
        self.assertEqual(ps.item(CONTRACT).status, STATUS_PRINTED)
        events = self.audit.read_events()
        voided = [e for e in events if e.event_type == "document_voided"]
        self.assertEqual(len(voided), 1)
        self.assertEqual(
            voided[0].details["reason"], "Paper jam damaged the form"
        )
        self.assertIn("document_reprinted", self.audit_types())

    def test_voiding_an_unprinted_item_raises(self):
        ps = self.make_print_session(self.make_deal())
        with self.assertRaises(PrintPipelineError):
            ps.void_item("buyers_order", reason="not printed yet")


class TestNoStockGate(PrintPipelineTestCase):
    """Stage 5 (decision 12): the long-stock confirmation gate is
    RETIRED. The Stage 4 tests here asserted a declined confirmation
    sent nothing for the contract; with every document on letter
    paper there is no special stock to confirm, so the contract
    prints regardless of the callback -- and with no callback at all.
    """

    def test_declining_callback_no_longer_blocks_the_contract(self):
        ps = self.make_print_session(self.make_deal(), stock=False)
        item = ps.print_item(CONTRACT)
        self.assertEqual(item.status, STATUS_PRINTED)
        self.assertEqual(len(self.provider.jobs_for(CONTRACT)), 1)

    def test_no_callback_at_all_prints_the_contract(self):
        ps = PrintSession.from_setup(
            deal=self.make_deal(),
            setup=self.setup_path,
            print_config=PrintConfig(path=self.config_path),
            session_dir=self.sessions_dir,
            provider=self.provider,
            audit_log=self.audit,
            actor=self.fi,
        )
        item = ps.print_item(CONTRACT)
        self.assertEqual(item.status, STATUS_PRINTED)
        self.assertEqual(len(self.provider.jobs_for(CONTRACT)), 1)


class TestCalibrationInJobs(PrintPipelineTestCase):
    def test_offsets_reach_the_job_and_payload_and_persist(self):
        config = PrintConfig(path=self.config_path)
        config.set_offsets(LETTER_PRINTER, "buyers_order", 1.5, -0.5)
        ps = self.make_print_session(self.make_deal(), config=config)
        ps.print_item("buyers_order")
        job = self.provider.jobs_for("buyers_order")[0]
        self.assertEqual(job.offsets_mm, {"x_mm": 1.5, "y_mm": -0.5})
        self.assertIn("Alignment offset applied", job.payload)
        reloaded = PrintConfig.load(self.config_path)
        self.assertEqual(
            reloaded.get_offsets(LETTER_PRINTER, "buyers_order"),
            {"x_mm": 1.5, "y_mm": -0.5},
        )


class TestPersistence(PrintPipelineTestCase):
    def test_reload_half_finished_session_and_resume_to_completion(self):
        deal = self.make_deal()
        provider = FakePrinterProvider(fail_on={"buyers_order"})
        ps = self.make_print_session(deal, provider=provider)
        ps.print_all()  # contract printed, buyers_order failed, rest pending
        statuses_before = {
            doc_id: item.status for doc_id, item in ps.items.items()
        }
        # Reload from disk with a fresh provider and finish the pack.
        provider2 = FakePrinterProvider()
        loaded_deal = DealSession.load(
            deal.deal_id, user=self.fi, preset=self.preset,
            deals_dir=self.deals_dir, audit_log=self.audit,
        )
        ps2 = PrintSession.load(
            deal.deal_id,
            deal=loaded_deal,
            setup=self.setup_path,
            print_config=PrintConfig(path=self.config_path),
            session_dir=self.sessions_dir,
            provider=provider2,
            audit_log=self.audit,
            actor=self.fi,
            stock_confirmation=lambda doc: True,
        )
        statuses_after = {
            doc_id: item.status for doc_id, item in ps2.items.items()
        }
        self.assertEqual(statuses_before, statuses_after)
        ps2.resume()
        ps2.pull_secure_item("secure_poa")
        ps2.pull_secure_item("secure_reassignment")
        self.assertFalse(ps2.is_complete)
        self.finish_packaging(ps2)
        self.assertTrue(ps2.is_complete)
        # The already-printed contract was NOT re-sent after reload.
        self.assertEqual(provider2.jobs_for(CONTRACT), [])
        self.assertIn("pack_print_completed", self.audit_types())


class TestUsedVehiclePack(PrintPipelineTestCase):
    def test_used_vehicle_pack_prints_buyers_guide(self):
        ps = self.make_print_session(self.make_deal(is_new=False))
        self.assertIn("buyers_guide", ps.items)
        self.finish_pack(ps)
        self.assertEqual(len(self.provider.jobs_for("buyers_guide")), 1)
        self.finish_packaging(ps)
        self.assertTrue(ps.is_complete)


if __name__ == "__main__":
    unittest.main()
