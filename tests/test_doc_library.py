"""Tests for the Stage 5 dealer document library (doc_library.py),
the values-overlay pack integration, and the print pipeline printing
a library document end to end (overlay PDF bytes to a fake printer).

Headless: the library is plain files (a JSON index + stored PDF
copies in a temp dir); users are a real Stage 3 user store so the
approve role check runs the real thing; deals are real DealSessions
on the repo's SAMPLE preset.

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
from dealerpaperwork.deal_flow import DealSession
from dealerpaperwork.doc_library import (
    CANONICAL_FIELDS,
    DocumentLibrary,
    DocumentLibraryError,
    FieldPlacement,
    resolve_field_values,
    validate_placements,
)
from dealerpaperwork.models import Buyer, DealInputs, TradeIn, Vehicle
from dealerpaperwork.pack import KIND_LIBRARY_DOC, build_pack
from dealerpaperwork.print_config import PrintConfig
from dealerpaperwork.print_pipeline import PrintSession
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.users import UserStore

D = Decimal
REPO_ROOT = Path(__file__).resolve().parents[1]
JPEG_BYTES = (REPO_ROOT / "tests/fixtures/form_images/page-1.jpg").read_bytes()
LETTER_PRINTER = "FakePrinter - F&I (Letter)"


def write_setup_file(directory: Path, *, with_trays: bool = False) -> Path:
    """A completed-setup file embedding the repo's SAMPLE preset."""
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


class LibraryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.library_dir = root / "documents"
        self.library = DocumentLibrary(self.library_dir)
        self.source = root / "we-owe.jpg"
        self.source.write_bytes(JPEG_BYTES)
        self.audit = AuditLog(root / "audit.jsonl")
        store = UserStore(root / "users.json", audit_log=self.audit)
        self.admin = store.create_user(
            actor=None, username="root", display_name="Root Admin",
            role="admin", secret="pw-root",
        )
        self.manager = store.create_user(
            actor=self.admin, username="mgr", display_name="Mary Manager",
            role="manager", secret="pw-mgr",
        )
        self.fi = store.create_user(
            actor=self.admin, username="fi1", display_name="Fred FandI",
            role="fi", secret="pw-fi",
        )
        self.sales = store.create_user(
            actor=self.admin, username="sam", display_name="Sam Sales",
            role="sales", secret="pw-sam",
        )

    def add_doc(self, doc_id="we-owe", conditions=("retail_new",), fields=()):
        return self.library.add_document(
            source_paths=[self.source],
            doc_id=doc_id,
            title="We Owe Form",
            when_it_prints=conditions,
            created_by="sam",
            fields=list(fields),
        )


class TestAddDocument(LibraryTestCase):
    def test_add_copies_the_jpeg_and_starts_as_draft(self):
        doc = self.add_doc()
        self.assertEqual(doc.status, "draft")
        stored = self.library.page_image_path(doc, 1)
        self.assertTrue(stored.exists())
        self.assertEqual(stored.read_bytes(), JPEG_BYTES)

    def test_non_jpeg_rejected(self):
        other = Path(self.tmp.name) / "form.docx"
        other.write_bytes(b"nope")
        with self.assertRaises(DocumentLibraryError) as ctx:
            self.library.add_document(
                source_paths=[other], doc_id="x", title="X",
                when_it_prints=("retail_new",), created_by="sam",
            )
        self.assertIn("JPEG", str(ctx.exception))

    def test_uppercase_jpeg_extension_accepted(self):
        upper = Path(self.tmp.name) / "FORM.JPEG"
        upper.write_bytes(JPEG_BYTES)
        doc = self.library.add_document(
            source_paths=[upper], doc_id="upper", title="Upper",
            when_it_prints=("retail_new",), created_by="sam",
        )
        self.assertEqual(doc.doc_id, "upper")

    def test_duplicate_doc_id_rejected(self):
        self.add_doc()
        with self.assertRaises(DocumentLibraryError) as ctx:
            self.add_doc()
        self.assertIn("already in the library", str(ctx.exception))

    def test_unknown_condition_rejected(self):
        with self.assertRaises(DocumentLibraryError):
            self.add_doc(conditions=("service",))

    def test_library_persists_across_instances(self):
        self.add_doc()
        reloaded = DocumentLibrary(self.library_dir)
        self.assertEqual([d.doc_id for d in reloaded.list_documents()], ["we-owe"])


class TestPlacements(LibraryTestCase):
    def test_unknown_field_rejected(self):
        errors = validate_placements([FieldPlacement("buyer_ssn", 1, 10, 10, 10)])
        self.assertTrue(any("not a field the app can fill" in e for e in errors))
        with self.assertRaises(DocumentLibraryError):
            self.add_doc(fields=[FieldPlacement("buyer_ssn", 1, 10, 10, 10)])

    def test_off_page_coordinates_rejected(self):
        cases = [
            FieldPlacement("buyer_name", 1, 300.0, 10.0, 10),   # x beyond 216mm
            FieldPlacement("buyer_name", 1, 10.0, 400.0, 10),   # y beyond 279mm
            FieldPlacement("buyer_name", 1, -5.0, 10.0, 10),    # negative x
            FieldPlacement("buyer_name", 0, 10.0, 10.0, 10),    # page is 1-based
            FieldPlacement("buyer_name", 1, 10.0, 10.0, 200),   # font size absurd
        ]
        for placement in cases:
            with self.subTest(placement=placement):
                self.assertTrue(validate_placements([placement]))
                with self.assertRaises(DocumentLibraryError):
                    self.library.update_placements(
                        self.add_doc(
                            doc_id=f"doc-{cases.index(placement)}"
                        ).doc_id,
                        [placement],
                    )

    def test_canonical_fields_start_with_the_briefed_twenty(self):
        # The Stage 4 twenty stay first and unchanged; the 2026-11-18
        # additions follow (tests/test_field_catalog.py).
        self.assertGreater(len(CANONICAL_FIELDS), 20)
        for expected in ("buyer_name", "vehicle_vin", "sales_tax_total",
                         "amount_financed", "dealer_name"):
            self.assertIn(expected, CANONICAL_FIELDS)

    def test_update_placements_replaces_and_persists(self):
        doc = self.add_doc()
        updated = self.library.update_placements(
            doc.doc_id, [FieldPlacement("buyer_name", 1, 20, 30, 11)]
        )
        self.assertEqual(len(updated.fields), 1)
        reloaded = DocumentLibrary(self.library_dir).get_document("we-owe")
        self.assertEqual(reloaded.fields[0].field_name, "buyer_name")


class TestApproval(LibraryTestCase):
    def test_only_manager_or_admin_may_approve(self):
        doc = self.add_doc()
        with self.assertRaises(DocumentLibraryError) as ctx:
            self.library.approve_document(doc.doc_id, self.fi)
        self.assertIn("manager or an admin", str(ctx.exception))
        with self.assertRaises(DocumentLibraryError):
            self.library.approve_document(doc.doc_id, self.sales)
        approved = self.library.approve_document(doc.doc_id, self.manager)
        self.assertTrue(approved.is_approved)
        self.assertEqual(approved.approved_by, "Mary Manager")
        self.assertTrue(approved.approved_at)

    def test_editing_an_approved_document_returns_it_to_draft(self):
        doc = self.add_doc()
        self.library.approve_document(doc.doc_id, self.manager)
        edited = self.library.update_placements(
            doc.doc_id, [FieldPlacement("buyer_name", 1, 20, 30, 11)]
        )
        self.assertEqual(edited.status, "draft")
        self.assertIsNone(edited.approved_by)


def inputs_for(is_new: bool) -> DealInputs:
    return DealInputs(
        vehicle=Vehicle(
            year=2026, make="Sample", model="Wagon", vin="VIN1",
            is_new=is_new,
        ),
        selling_price=D("35000"),
        dealer_fee=D("1199"),
        county_surtax_rate=D("0.015"),
    )


class TestDocumentsForPack(LibraryTestCase):
    def test_unapproved_never_returned(self):
        self.add_doc()
        self.assertEqual(self.library.documents_for_pack(inputs_for(True)), [])

    def test_approved_matches_new_vs_used(self):
        docs = [
            self.add_doc("for-new", ("retail_new",)),
            self.add_doc("for-used", ("retail_used",)),
            self.add_doc("for-both", ("retail_new", "retail_used")),
            self.add_doc("for-lease", ("lease",)),
        ]
        for doc in docs:
            self.library.approve_document(doc.doc_id, self.manager)
        new_ids = [d.doc_id for d in self.library.documents_for_pack(inputs_for(True))]
        used_ids = [d.doc_id for d in self.library.documents_for_pack(inputs_for(False))]
        self.assertEqual(new_ids, ["for-new", "for-both"])
        self.assertEqual(used_ids, ["for-used", "for-both"])

    def test_build_pack_appends_approved_library_documents(self):
        doc = self.add_doc(fields=[FieldPlacement("buyer_name", 1, 20, 30, 11)])
        self.library.approve_document(doc.doc_id, self.manager)
        pack = build_pack(inputs_for(True), library=self.library)
        library_entries = [d for d in pack if d.kind == KIND_LIBRARY_DOC]
        self.assertEqual(len(library_entries), 1)
        entry = library_entries[0]
        self.assertEqual(entry.doc_id, "we-owe")
        self.assertIs(entry.library_document, self.library.get_document("we-owe"))
        self.assertEqual(entry.doc_class, "standard_letter")
        # And without a library, the pack is unchanged (backward compatible).
        plain = build_pack(inputs_for(True))
        self.assertEqual([d for d in plain if d.kind == KIND_LIBRARY_DOC], [])


class TestResolveFieldValues(LibraryTestCase):
    def test_values_come_from_the_deal(self):
        root = Path(self.tmp.name)
        setup_path = write_setup_file(root)
        session = DealSession.start(
            self.fi, setup_path=setup_path,
            deals_dir=root / "deals", audit_log=self.audit,
        )
        session.set_buyer(Buyer(
            full_name="Ada Buyer", street_address="2 Oak Ave",
            city="Sample City", state="FL", zip_code="00000",
            residence_county="Sample County",
        ))
        session.set_vehicle(Vehicle(2026, "Sample", "Wagon", "VIN123", True))
        session.set_trade(TradeIn(
            description="2019 sample trade", allowance=D("10000"), payoff=D("0"),
        ))
        session.set_money(selling_price=D("35000"))
        values = resolve_field_values(session)
        self.assertEqual(values["buyer_name"], "Ada Buyer")
        self.assertEqual(values["buyer_address"], "2 Oak Ave, Sample City, FL 00000")
        self.assertEqual(values["buyer_county"], "Sample County")
        self.assertEqual(values["vehicle_vin"], "VIN123")
        self.assertEqual(values["vehicle_year"], "2026")
        self.assertEqual(values["trade_description"], "2019 sample trade")
        self.assertEqual(values["trade_allowance"], "10,000.00")
        self.assertEqual(values["dealer_fee"], "1,199.00")
        self.assertEqual(values["selling_price"], "35,000.00")
        # The Section 9 fixture amounts with the preset's $199 tag fee
        # taxed (Decision 14): 26,398 base -> 1,583.88 + 75.00.
        self.assertEqual(values["sales_tax_total"], "1,658.88")
        self.assertEqual(values["dealer_name"], "Sample Motors LLC (SAMPLE)")
        self.assertEqual(
            values["dealer_address"], "100 Sample Street, Sample City, FL 00000"
        )
        # Every canonical field resolves (possibly to "").
        self.assertEqual(set(values), set(CANONICAL_FIELDS))


class TestPipelinePrintsLibraryDocument(LibraryTestCase):
    def test_library_document_prints_as_overlay_pdf_bytes(self):
        root = Path(self.tmp.name)
        doc = self.add_doc(fields=[
            FieldPlacement("buyer_name", 1, 20, 30, 11),
            FieldPlacement("vehicle_vin", 1, 20, 45, 10),
        ])
        self.library.approve_document(doc.doc_id, self.manager)
        setup_path = write_setup_file(root, with_trays=True)
        session = DealSession.start(
            self.fi, setup_path=setup_path,
            deals_dir=root / "deals", audit_log=self.audit,
        )
        session.set_buyer(Buyer(
            full_name="Ada Buyer", residence_county="Sample County",
        ))
        session.set_vehicle(Vehicle(2026, "Sample", "Wagon", "VIN123", True))
        session.set_money(selling_price=D("35000"))
        provider = FakePrinterProvider([LETTER_PRINTER])
        ps = PrintSession.from_setup(
            deal=session,
            setup=setup_path,
            print_config=PrintConfig(path=root / "print_config.json"),
            session_dir=root / "print_sessions",
            provider=provider,
            audit_log=self.audit,
            actor=self.fi,
            library=self.library,
        )
        ps.print_all()
        jobs = provider.jobs_for("we-owe")
        self.assertEqual(len(jobs), 1)
        payload = jobs[0].payload
        self.assertIsInstance(payload, bytes)
        self.assertTrue(payload.startswith(b"%PDF-"))
        self.assertIn(b"Ada Buyer", payload)
        # Uncertified deal: the overlay carries the DRAFT banner.
        self.assertIn(b"DRAFT", payload)


if __name__ == "__main__":
    unittest.main()
