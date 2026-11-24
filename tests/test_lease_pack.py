"""Tests for the Stage 6 lease print pack (pack.build_lease_pack,
doc_library lease condition, print_pipeline dispatch).

Headless: FakePrinterProvider only. A lease pack is the lease
worksheet print item plus APPROVED library documents whose
condition is `lease`; retail-condition documents must never join
it, and a certified lease pack prints clean through the Stage 4/5
pipeline while an uncertified one prints DRAFT-flagged.

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
from dealerpaperwork.deal_flow import preset_from_setup_file
from dealerpaperwork.doc_library import (
    CONDITION_LEASE,
    CONDITION_RETAIL_NEW,
    DocumentLibrary,
    FieldPlacement,
)
from dealerpaperwork.lease_flow import LeaseDealSession
from dealerpaperwork.models import Buyer, Vehicle
from dealerpaperwork.pack import KIND_LIBRARY_DOC, build_lease_pack
from dealerpaperwork.print_config import PrintConfig
from dealerpaperwork.print_pipeline import PrintSession
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.users import UserStore

D = Decimal
REPO_ROOT = Path(__file__).resolve().parents[1]
LETTER_PRINTER = "FakePrinter - Office (Letter)"


def write_setup_file(directory: Path) -> Path:
    """A completed-setup file embedding the repo's SAMPLE preset
    plus a SAMPLE lease section (test data only; same shape as
    tests/test_lease_flow.py's helper)."""
    sample = json.loads(
        (REPO_ROOT / "config" / "sample_preset.json").read_text(encoding="utf-8")
    )
    sample.pop("_comment", None)
    sample["lease"] = {
        "lessor_name": "Sample Lessor (SAMPLE)",
        "title_holder": "Sample Lessor (SAMPLE)",
        "acquisition_fee": "650",
        "cap_fees": "60",
        "upfront_charges": "400.00",
    }
    setup = {
        "state": "complete",
        "completed": True,
        "completed_at": "2026-01-01T10:00:00",
        "preset": sample,
    }
    path = Path(directory) / "setup.json"
    path.write_text(json.dumps(setup), encoding="utf-8")
    return path


def tray_mapping() -> dict:
    return {
        "standard_letter": {
            "document_class": "standard_letter",
            "printer_name": LETTER_PRINTER,
            "tray": "Tray 1",
            "paper": "Letter",
            "alignment_offsets_mm": {},
        }
    }


class LeasePackTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        # Setup file carries the tray mapping for the pipeline tests.
        self.setup_path = write_setup_file(base)
        setup_data = json.loads(self.setup_path.read_text(encoding="utf-8"))
        setup_data["tray_mapping"] = tray_mapping()
        setup_data["selected_printer"] = LETTER_PRINTER
        self.setup_path.write_text(json.dumps(setup_data), encoding="utf-8")
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
        self.manager = SimpleNamespace(
            role="manager", display_name="Manny Manager", username="manny"
        )
        self.preset = preset_from_setup_file(self.setup_path)
        self.deals_dir = base / "deals"
        self.sessions_dir = base / "print_sessions"
        self.provider = FakePrinterProvider()
        self.config_path = base / "print_config.json"
        # A one-page stand-in PDF for library documents.
        self.form_pdf = base / "form.jpg"
        self.form_pdf.write_bytes((REPO_ROOT / "tests/fixtures/form_images/page-1.jpg").read_bytes())

    # -- helpers ---------------------------------------------------------

    def make_library(self) -> DocumentLibrary:
        library = DocumentLibrary(Path(self.tmp.name) / "documents")
        library.add_document(
            source_paths=[self.form_pdf], doc_id="sample-lease-form",
            title="Sample Lease Form (SAMPLE)",
            when_it_prints=(CONDITION_LEASE,), created_by="fay",
            fields=[FieldPlacement("buyer_name", page=1, x_mm=20, y_mm=30)],
        )
        library.approve_document("sample-lease-form", self.manager)
        library.add_document(
            source_paths=[self.form_pdf], doc_id="sample-retail-form",
            title="Sample Retail Form (SAMPLE)",
            when_it_prints=(CONDITION_RETAIL_NEW,), created_by="fay",
            fields=[FieldPlacement("buyer_name", page=1, x_mm=20, y_mm=30)],
        )
        library.approve_document("sample-retail-form", self.manager)
        library.add_document(
            source_paths=[self.form_pdf], doc_id="draft-lease-form",
            title="Draft Lease Form (SAMPLE)",
            when_it_prints=(CONDITION_LEASE,), created_by="fay",
            fields=[FieldPlacement("buyer_name", page=1, x_mm=20, y_mm=30)],
        )
        return library

    def make_lease_deal(self, *, certify: bool = True) -> LeaseDealSession:
        session = LeaseDealSession.start(
            self.fi, setup_path=self.setup_path,
            deals_dir=self.deals_dir, audit_log=self.audit,
        )
        session.set_buyer(
            Buyer(full_name="Sample Buyer", residence_county="Sample County")
        )
        session.advance()
        session.set_vehicle(
            Vehicle(
                year=2026, make="Sample", model="Vehicle",
                vin="1SAMPLEVIN0000000", is_new=True,
            )
        )
        session.advance()
        session.set_lease(
            msrp="30000", dealer_discount="1500",
            residual_base_msrp="30000", residual_percent="0.62",
            term_months=36, money_factor="0.00150",
        )
        session.advance()  # computes -> review
        if certify:
            session.certify()
        return session

    def make_print_session(self, deal, *, library=None) -> PrintSession:
        return PrintSession.from_setup(
            deal=deal,
            setup=self.setup_path,
            print_config=PrintConfig(path=self.config_path),
            session_dir=self.sessions_dir,
            provider=self.provider,
            audit_log=self.audit,
            actor=self.fi,
            library=library,
        )


class TestLeasePackComposition(LeasePackTestCase):
    def test_pack_is_worksheet_plus_lease_documents_only(self):
        deal = self.make_lease_deal()
        pack = build_lease_pack(deal, library=self.make_library())
        ids = [doc.doc_id for doc in pack]
        self.assertEqual(ids, ["lease_worksheet", "sample-lease-form", "paper_packaging_checklist"])
        self.assertNotIn("sample-retail-form", ids)
        self.assertNotIn("draft-lease-form", ids)
        self.assertNotIn("retail_installment_contract", ids)
        lease_doc = pack[1]
        self.assertEqual(lease_doc.kind, KIND_LIBRARY_DOC)
        self.assertIsNotNone(lease_doc.library_document)

    def test_pack_without_library_is_worksheet_only(self):
        deal = self.make_lease_deal()
        pack = build_lease_pack(deal)
        self.assertEqual([doc.doc_id for doc in pack], ["lease_worksheet", "paper_packaging_checklist"])


class TestLeasePackPrinting(LeasePackTestCase):
    def test_changed_library_artwork_requires_reopening_pack(self):
        from dealerpaperwork.print_pipeline import PrintPipelineError
        deal = self.make_lease_deal(certify=True)
        library = self.make_library()
        ps = self.make_print_session(deal, library=library)
        document = next(d.library_document for d in ps.documents if d.kind == KIND_LIBRARY_DOC)
        library.page_image_path(document, 1).write_bytes(b'changed artwork')
        with self.assertRaises(PrintPipelineError):
            ps.print_all()
        self.assertEqual(self.provider.jobs, [])

    def test_certified_lease_pack_prints_clean(self):
        deal = self.make_lease_deal(certify=True)
        ps = self.make_print_session(deal, library=self.make_library())
        self.assertFalse(ps.draft_only)
        self.assertEqual(
            [doc.doc_id for doc in ps.documents],
            ["lease_worksheet", "sample-lease-form", "paper_packaging_checklist"],
        )
        ps.print_all()
        self.assertFalse(ps.is_complete)
        for row in ps.packaging_rows:
            ps.check_packaging_item(row.item_id, actor=self.fi, reference='SAMPLE paper original 001')
        self.assertTrue(ps.is_complete)
        jobs = {job.doc_id: job for job in self.provider.jobs}
        self.assertIn("lease_worksheet", jobs)
        self.assertNotIn("DRAFT", jobs["lease_worksheet"].payload)
        # The library document prints as overlay PDF bytes.
        self.assertIn("sample-lease-form", jobs)
        payload = jobs["sample-lease-form"].payload
        payload_bytes = (
            payload if isinstance(payload, bytes) else payload.encode("utf-8")
        )
        self.assertTrue(payload_bytes.startswith(b"%PDF-"))

    def test_uncertified_lease_pack_prints_draft(self):
        deal = self.make_lease_deal(certify=False)
        ps = self.make_print_session(deal)
        self.assertTrue(ps.draft_only)
        ps.print_all()
        self.assertTrue(self.provider.jobs)
        for job in self.provider.jobs:
            payload = job.payload
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="replace")
            self.assertIn("DRAFT", payload)


if __name__ == "__main__":
    unittest.main()
