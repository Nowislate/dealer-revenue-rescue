"""Tests for Stage 5 drill mode (drill.py).

A drill is a SAMPLE deal end to end: prefilled with the Section 9
fixture amounts (so it re-verifies the math), watermarked SAMPLE
forever -- certified or not -- excluded from the normal deal list,
and recorded as a run (duration + fixture verification) when it
finishes.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.audit import AuditLog
from dealerpaperwork.deal_flow import DealSession, list_deals, preset_from_setup_file
from dealerpaperwork.drill import (
    SAMPLE_BUYER_NAME,
    drill_runs,
    finish_drill,
    fixture_tax_verified,
    last_drill_summary,
    readiness_line,
    run_drill,
    start_drill,
)
from dealerpaperwork.print_config import PrintConfig
from dealerpaperwork.print_pipeline import PrintSession
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.users import UserStore

REPO_ROOT = Path(__file__).resolve().parents[1]
LETTER_PRINTER = "FakePrinter - F&I (Letter)"


def write_setup_file(directory: Path, *, with_trays: bool = False) -> Path:
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
    if with_trays:
        setup["selected_printer"] = LETTER_PRINTER
        setup["tray_mapping"] = {
            "standard_letter": {
                "document_class": "standard_letter",
                "printer_name": LETTER_PRINTER,
                "tray": "Tray 1",
                "paper": "Letter",
                "alignment_offsets_mm": {},
            }
        }
    path = Path(directory) / "setup.json"
    path.write_text(json.dumps(setup), encoding="utf-8")
    return path


class DrillTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root = root
        self.setup_path = write_setup_file(root, with_trays=True)
        self.audit = AuditLog(root / "audit.jsonl")
        store = UserStore(root / "users.json", audit_log=self.audit)
        admin = store.create_user(
            actor=None, username="root", display_name="Root Admin",
            role="admin", secret="pw-root",
        )
        self.fi = store.create_user(
            actor=admin, username="fay", display_name="Fay F&I",
            role="fi", secret="pw-fi",
        )
        self.preset = preset_from_setup_file(self.setup_path)
        self.deals_dir = root / "deals"
        self.runs_path = root / "drill_runs.json"

    def start(self) -> DealSession:
        return start_drill(
            self.fi,
            preset=self.preset,
            setup_path=self.setup_path,
            deals_dir=self.deals_dir,
            audit_log=self.audit,
        )


class TestStartDrill(DrillTestCase):
    def test_drill_is_sample_data_on_the_fixture_amounts(self):
        session = self.start()
        self.assertTrue(session.is_drill)
        self.assertEqual(session.inputs.buyer.full_name, SAMPLE_BUYER_NAME)
        self.assertEqual(session.inputs.selling_price, Decimal("35000"))
        self.assertEqual(session.inputs.dealer_fee, Decimal("1199"))
        self.assertEqual(session.inputs.trade.allowance, Decimal("10000"))
        # The drill re-verifies the math: the fixture total tax
        # (Decision 14 -- the preset's $199 tag fee is taxed).
        self.assertEqual(session.worksheet_result.total_tax, Decimal("1658.88"))
        self.assertTrue(fixture_tax_verified(session))

    def test_drill_watermark_is_sample(self):
        session = self.start()
        self.assertEqual(session.watermark, "SAMPLE")

    def test_certified_drill_still_renders_sample(self):
        session = self.start()
        for _ in range(4):  # buyer -> vehicle -> trade -> worksheet -> review
            self.assertEqual(session.advance(), [])
        session.certify()
        self.assertTrue(session.is_certified)
        self.assertEqual(session.watermark, "SAMPLE")


class TestDrillListings(DrillTestCase):
    def test_drills_excluded_from_normal_listing(self):
        drill = self.start()
        real = DealSession.start(
            self.fi, setup_path=self.setup_path,
            deals_dir=self.deals_dir, audit_log=self.audit,
        )
        normal = list_deals(self.deals_dir)
        self.assertEqual([s["deal_id"] for s in normal], [real.deal_id])
        everything = list_deals(self.deals_dir, include_drills=True)
        self.assertEqual(len(everything), 2)
        drill_summary = next(s for s in everything if s["deal_id"] == drill.deal_id)
        self.assertTrue(drill_summary["is_drill"])
        self.assertEqual(drill_summary["watermark"], "SAMPLE")


class TestDrillPrinting(DrillTestCase):
    def make_print_session(self, session, provider):
        return PrintSession.from_setup(
            deal=session,
            setup=self.setup_path,
            print_config=PrintConfig(path=self.root / "print_config.json"),
            session_dir=self.root / "print_sessions",
            provider=provider,
            audit_log=self.audit,
            actor=self.fi,
        )

    def test_drill_prints_carry_sample_on_every_page(self):
        session = self.start()
        provider = FakePrinterProvider([LETTER_PRINTER])
        ps = self.make_print_session(session, provider)
        self.assertTrue(ps.sample_only)
        ps.print_all()
        self.assertTrue(provider.jobs)
        for job in provider.jobs:
            self.assertIn("SAMPLE", job.payload)

    def test_certified_drill_prints_sample_never_draft_or_clean(self):
        session = self.start()
        for _ in range(4):  # walk to the review step
            self.assertEqual(session.advance(), [])
        session.certify()
        provider = FakePrinterProvider([LETTER_PRINTER])
        ps = self.make_print_session(session, provider)
        ps.print_all()
        self.assertTrue(provider.jobs)
        for job in provider.jobs:
            self.assertIn("SAMPLE", job.payload)
            self.assertNotIn("DRAFT", job.payload)


class TestDrillRuns(DrillTestCase):
    def test_completed_run_records_duration_and_fixture(self):
        session = self.start()
        record = finish_drill(
            session,
            runs_path=self.runs_path,
            print_session=SimpleNamespace(is_complete=True),
        )
        self.assertEqual(record["user"], "fay")
        self.assertEqual(record["outcome"], "completed")
        self.assertTrue(record["fixture_tax_verified"])
        self.assertGreaterEqual(record["duration_seconds"], 0)
        self.assertTrue(record["started_at"])
        self.assertTrue(record["completed_at"])
        self.assertEqual(len(drill_runs(self.runs_path)), 1)
        summary = last_drill_summary(self.runs_path)
        self.assertEqual(summary["user"], "fay")
        self.assertEqual(summary["outcome"], "completed")
        self.assertTrue(summary["fixture_tax_verified"])
        self.assertEqual(summary["last_drill_at"], record["completed_at"])

    def test_ended_run_without_print_session(self):
        session = self.start()
        record = finish_drill(session, runs_path=self.runs_path)
        self.assertEqual(record["outcome"], "ended")
        self.assertTrue(record["fixture_tax_verified"])

    def test_no_runs_yet(self):
        self.assertEqual(drill_runs(self.runs_path), [])
        self.assertIsNone(last_drill_summary(self.runs_path))



class TestRunDrill(DrillTestCase):
    """The home screen's Practice drill button runs run_drill with the
    real walk-through and print windows as the two callbacks."""

    def run_with(self, walk, print_pack):
        return run_drill(
            self.fi, preset=self.preset, setup_path=self.setup_path,
            walk=walk, print_pack=print_pack, deals_dir=self.deals_dir,
            audit_log=self.audit, runs_path=self.runs_path,
        )

    def test_walk_certify_print_records_a_completed_run(self):
        provider = FakePrinterProvider([LETTER_PRINTER])
        seen = {}

        def walk(session):
            seen["walked"] = session.deal_id
            for _ in range(4):
                self.assertEqual(session.advance(), [])
            session.certify()

        def print_pack(session):
            # The reloaded deal is the one the user left: certified,
            # still a drill, still SAMPLE.
            self.assertEqual(session.deal_id, seen["walked"])
            self.assertTrue(session.is_certified)
            self.assertTrue(session.is_drill)
            ps = PrintSession.from_setup(
                deal=session, setup=self.setup_path,
                print_config=PrintConfig(path=self.root / "print_config.json"),
                session_dir=self.root / "print_sessions", provider=provider,
                audit_log=self.audit, actor=self.fi,
            )
            ps.print_all()
            for item in list(ps.items.values()):
                if not item.is_done:
                    ps.pull_secure_item(item.doc_id)
            return ps

        record = self.run_with(walk, print_pack)
        self.assertTrue(provider.jobs)
        for job in provider.jobs:
            self.assertIn("SAMPLE", job.payload)
        self.assertEqual(record["deal_id"], seen["walked"])
        self.assertEqual(record["outcome"], "completed")
        self.assertTrue(record["fixture_tax_verified"])
        self.assertEqual(len(drill_runs(self.runs_path)), 1)
        # The drill never reaches the normal deal list.
        self.assertEqual(list_deals(self.deals_dir), [])

    def test_skipping_the_print_records_an_ended_run(self):
        record = self.run_with(lambda s: None, lambda s: None)
        self.assertEqual(record["outcome"], "ended")
        self.assertEqual(last_drill_summary(self.runs_path)["outcome"], "ended")

    def test_a_crash_in_the_walk_still_records_the_run(self):
        def walk(session):
            raise RuntimeError("window closed unexpectedly")

        with self.assertRaises(RuntimeError):
            self.run_with(walk, lambda s: None)
        self.assertEqual(len(drill_runs(self.runs_path)), 1)


class TestHomeButton(DrillTestCase):
    def test_practice_drill_button_routes_to_the_drill(self):
        from unittest.mock import patch
        from dealerpaperwork import app_ui
        if not app_ui.TK_AVAILABLE:
            self.skipTest("Tkinter import needed; no display/window is used")
        app = object.__new__(app_ui.Application)
        app.flow = SimpleNamespace(require_access=lambda: self.fi, stage="home")
        with patch.object(app_ui.Application, "open_drill") as open_drill:
            app.open_action("drill")
        open_drill.assert_called_once_with()


class TestReadinessLine(unittest.TestCase):
    def test_no_drill_on_record_asks_for_one(self):
        self.assertIn("No practice drill on record", readiness_line(None))

    def test_completed_run_names_when_who_and_result(self):
        line = readiness_line({
            "last_drill_at": "2026-11-19T10:30:12-05:00", "user": "fay",
            "outcome": "completed", "fixture_tax_verified": True,
        })
        self.assertIn("2026-11-19 10:30", line)
        self.assertIn("fay", line)
        self.assertIn("printed in full", line)
        self.assertIn("sample tax check matched", line)

    def test_unmatched_tax_check_is_not_called_a_failure(self):
        line = readiness_line({
            "last_drill_at": "2026-11-19T10:30:12", "user": "fay",
            "outcome": "ended", "fixture_tax_verified": False,
        })
        self.assertIn("not printed in full", line)
        self.assertNotIn("tax", line)


if __name__ == "__main__":
    unittest.main()
