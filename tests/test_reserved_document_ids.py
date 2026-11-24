"""Reserved IDs must never hide a real dealer form or collapse pack state."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from dealerpaperwork.doc_library import DealerDocument, DocumentLibrary, DocumentLibraryError
from dealerpaperwork.pack import PackEntry, build_pack, build_lease_pack
from dealerpaperwork.print_config import PrintConfig
from dealerpaperwork.print_pipeline import PrintSession
from test_print_pipeline import PrintPipelineTestCase
from test_lease_pack import LeasePackTestCase

# Hand-listed independently of the production reservation set.
RESERVED_IDS = (
    'retail_installment_contract', 'buyers_order', 'deal_worksheet',
    'title_application', 'odometer_statement', 'buyers_guide',
    'buyers_guide_note', 'secure_poa', 'secure_reassignment',
    'lease_worksheet', 'paper_packaging_checklist',
)
FORM = b'%PDF-1.4 synthetic dealer form'


def legacy_library(directory, doc_id, condition):
    """Write a pre-fix library without invoking the new-add validation."""
    directory.mkdir(parents=True, exist_ok=True)
    documents = [DealerDocument(
        doc_id=identifier, title='Synthetic dealer form',
        source_filename=f'{identifier}.pdf', stored_name=f'{identifier}.pdf',
        when_it_prints=(condition,), status='approved',
        created_by='Sample Manager', approved_by='Sample Manager',
    ) for identifier in (doc_id, 'unrelated-dealer-form')]
    for document in documents:
        (directory / document.stored_name).write_bytes(FORM)
    (directory / 'library.json').write_text(json.dumps({
        'format_version': 'stage5-1',
        'documents': [document.to_dict() for document in documents],
    }), encoding='utf-8')
    return DocumentLibrary(directory)


def file_snapshot(directory):
    return {p.relative_to(directory): p.read_bytes()
            for p in directory.rglob('*') if p.is_file()}


class ReservedDocumentIds(unittest.TestCase):
    def test_new_reserved_ids_are_rejected_before_copy_or_index_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = Path(__file__).parent / 'fixtures/form_images/page-1.jpg'
            for doc_id in RESERVED_IDS:
                with self.subTest(doc_id=doc_id):
                    library = DocumentLibrary(root / doc_id)
                    with self.assertRaisesRegex(DocumentLibraryError, 'reserved'):
                        library.add_document(source_paths=[source], doc_id=doc_id,
                                             title='Synthetic form')
                    self.assertFalse(library.library_dir.exists())

    def test_existing_reserved_ids_fail_closed_without_changing_library(self):
        with tempfile.TemporaryDirectory() as temp:
            for builder, condition in ((build_pack, 'retail_new'),
                                       (build_lease_pack, 'lease')):
                deal = SimpleNamespace(deal_kind='lease' if condition == 'lease' else 'retail')
                for doc_id in RESERVED_IDS:
                    with self.subTest(builder=builder.__name__, doc_id=doc_id):
                        directory = Path(temp) / condition / doc_id
                        library = legacy_library(directory, doc_id, condition)
                        before = file_snapshot(directory)
                        with self.assertRaisesRegex(ValueError, 'reserved') as caught:
                            builder(deal, library=library)
                        self.assertIn(doc_id, str(caught.exception))
                        self.assertIn('re-add', str(caught.exception))
                        self.assertEqual(file_snapshot(directory), before)
                        self.assertEqual(len(library.list_documents()), 2)

    def test_retail_duplicate_custom_entries_are_rejected(self):
        entry = PackEntry('custom-form', 'Synthetic form', 'standard_letter')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            build_pack(SimpleNamespace(), entries=(entry, entry))

    def test_both_builders_reject_duplicate_nonreserved_library_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            for builder, condition in ((build_pack, 'retail_new'),
                                       (build_lease_pack, 'lease')):
                with self.subTest(builder=builder.__name__):
                    library = legacy_library(Path(temp) / condition, 'dealer-form', condition)
                    # Caller-supplied records must not collapse when indexed by doc_id.
                    library.get_document('unrelated-dealer-form').doc_id = 'dealer-form'
                    deal = SimpleNamespace(deal_kind='lease' if condition == 'lease' else 'retail')
                    with self.assertRaisesRegex(ValueError, 'duplicate'):
                        builder(deal, library=library)

    def test_ordinary_dealer_documents_are_preserved_in_both_packs(self):
        with tempfile.TemporaryDirectory() as temp:
            for builder, condition in ((build_pack, 'retail_new'),
                                       (build_lease_pack, 'lease')):
                with self.subTest(builder=builder.__name__):
                    library = legacy_library(Path(temp) / condition, 'dealer-form', condition)
                    deal = SimpleNamespace(deal_kind='lease' if condition == 'lease' else 'retail')
                    documents = builder(deal, library=library)
                    ids = [document.doc_id for document in documents]
                    self.assertEqual(ids[-3:], ['dealer-form', 'unrelated-dealer-form',
                                               'paper_packaging_checklist'])
                    self.assertEqual(len(ids), len(set(ids)))


class RetailCollisionPrintTests(PrintPipelineTestCase):
    def test_existing_checklist_collision_cannot_overwrite_saved_session_or_print(self):
        deal = self.make_deal()
        saved = self.make_print_session(deal)
        before = saved.file_path.read_bytes()
        audit_before = self.audit.path.read_bytes()
        library = legacy_library(Path(self.tmp.name) / 'documents',
                                 'paper_packaging_checklist', 'retail_new')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            PrintSession.from_setup(deal=deal, setup=self.setup_path,
                                    print_config=PrintConfig(path=self.config_path),
                                    session_dir=self.sessions_dir, provider=self.provider,
                                    audit_log=self.audit, actor=self.fi, library=library)
        self.assertEqual(saved.file_path.read_bytes(), before)
        self.assertEqual(self.audit.path.read_bytes(), audit_before)
        self.assertEqual(self.provider.jobs, [])


class LeaseCollisionPrintTests(LeasePackTestCase):
    def test_existing_checklist_collision_cannot_create_print_session_or_print(self):
        deal = self.make_lease_deal()
        library = legacy_library(Path(self.tmp.name) / 'collision-documents',
                                 'paper_packaging_checklist', 'lease')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            self.make_print_session(deal, library=library)
        self.assertFalse(self.sessions_dir.exists())
        self.assertEqual(self.provider.jobs, [])
