"""The image port must preserve current packaging and session authority."""
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dealerpaperwork.doc_library import DocumentLibrary, FieldPlacement
from dealerpaperwork.packaging_checklist import CHECKLIST_ID, pack_fingerprint
from dealerpaperwork.print_pipeline import PrintSession, PrintPipelineError
from dealerpaperwork.print_config import PrintConfig
from test_print_pipeline import PrintPipelineTestCase
from test_lease_pack import LeasePackTestCase

FIX = Path(__file__).parent / 'fixtures/form_images'
MANAGER = SimpleNamespace(role='manager', display_name='Sample Manager')


class ImagePackagingCases:
    def fixture(self):
        deal = self.make_lease_deal() if isinstance(self, LeasePackTestCase) else self.make_deal()
        library = DocumentLibrary(Path(self.tmp.name) / 'combined-library')
        library.add_document(source_paths=[FIX / 'page-1.jpg', FIX / 'page-2.jpg'],
            doc_id='dealer-form', title='Synthetic form', when_it_prints=['lease', 'retail_new'],
            fields=[FieldPlacement('buyer_name', 1, 20, 55)])
        library.approve_document('dealer-form', MANAGER)
        session = PrintSession.from_setup(deal=deal, setup=self.setup_path,
            print_config=PrintConfig(path=self.config_path),
            session_dir=self.sessions_dir, provider=self.provider, audit_log=self.audit,
            actor=self.fi, library=library)
        return deal, library, session

    def reload(self, deal, library):
        from dealerpaperwork.print_config import PrintConfig
        return PrintSession.load(deal.deal_id, deal=deal, setup=self.setup_path,
            print_config=PrintConfig(path=self.config_path), session_dir=self.sessions_dir,
            provider=self.provider, audit_log=self.audit, actor=self.fi,
            library=DocumentLibrary(library.library_dir))

    def test_images_complete_with_named_checks_and_checklist_last(self):
        _, library, session = self.fixture()
        result = session.print_all()
        self.assertIsNone(result['failed'])
        self.assertEqual(self.provider.jobs[-1].doc_id, CHECKLIST_ID)
        self.assertIn((FIX / 'page-2.jpg').read_bytes(), self.provider.jobs_for('dealer-form')[0].payload)
        for document in session.documents:
            if document.kind == 'secure_stock':
                session.pull_secure_item(document.doc_id, actor=self.fi)
        self.assertFalse(session.is_complete)
        for row in session.packaging_rows:
            session.check_packaging_item(row.item_id, actor=self.fi, reference='Synthetic paper evidence')
        self.assertTrue(session.is_complete)
        self.assertEqual(session.item('dealer-form').job['library_revision'], library.get_document('dealer-form').image_revision)

    def test_fingerprint_binds_image_revision_and_manifest(self):
        deal, _, session = self.fixture()
        original = pack_fingerprint(deal, session.documents)
        changed = copy.deepcopy(session.documents)
        record = next(d.library_document for d in changed if d.doc_id == 'dealer-form')
        record.pages[0].sha256 = '0' * 64
        self.assertNotEqual(original, pack_fingerprint(deal, changed))

    def test_replacement_requires_reopen_and_preserves_superseded_checks(self):
        deal, library, session = self.fixture()
        session.print_all()
        for document in session.documents:
            if document.kind == 'secure_stock': session.pull_secure_item(document.doc_id, actor=self.fi)
        for row in session.packaging_rows:
            session.check_packaging_item(row.item_id, actor=self.fi, reference='Synthetic paper evidence')
        before = session.file_path.read_bytes()
        other = DocumentLibrary(library.library_dir)
        other.replace_page_images('dealer-form', [FIX / 'page-3.jpg'])
        other.approve_document('dealer-form', MANAGER)
        with self.assertRaises(PrintPipelineError): session.resume()
        self.assertEqual(session.file_path.read_bytes(), before)
        fresh = self.reload(deal, library)
        self.assertFalse(fresh.is_complete)
        self.assertEqual(fresh.packaging_checks, {})
        self.assertTrue(fresh.packaging_history[-1]['checks']['fi_signoff'])
        self.assertEqual(fresh.documents[-1].doc_id, CHECKLIST_ID)
        with self.assertRaises(PrintPipelineError): session.save()
        fresh.print_all()
        self.assertIn((FIX / 'page-3.jpg').read_bytes(), self.provider.jobs_for('dealer-form')[-1].payload)

    def test_unapproved_saved_obligation_survives_reload_before_checklist(self):
        deal, library, session = self.fixture()
        library.replace_page_images('dealer-form', [FIX / 'page-3.jpg'])
        fresh = self.reload(deal, library)
        self.assertIn('dealer-form', fresh.items)
        self.assertEqual(fresh.documents[-1].doc_id, CHECKLIST_ID)
        self.assertFalse(fresh.is_complete)
        with self.assertRaises(PrintPipelineError): fresh.resume()
        self.assertEqual(self.provider.jobs, [])

    def test_damage_to_completed_form_cannot_schedule_an_unmarked_duplicate(self):
        deal, library, session = self.fixture()
        session.print_item('dealer-form')
        path = library.page_image_path(library.get_document('dealer-form'), 2)
        approved_bytes = path.read_bytes()
        path.unlink()
        with self.assertRaises(PrintPipelineError): session.resume()
        self.assertEqual(session.item('dealer-form').status, 'printed')
        self.assertEqual(session.item('dealer-form').print_count, 1)
        self.assertFalse(session.item('dealer-form').reprint_pending)
        path.write_bytes(approved_bytes)
        fresh = self.reload(deal, library)
        fresh.resume()
        self.assertEqual(len(self.provider.jobs_for('dealer-form')), 1)
        self.assertEqual(fresh.item('dealer-form').print_count, 1)


class RetailImagePackaging(ImagePackagingCases, PrintPipelineTestCase):
    def test_new_to_used_revision_can_remove_an_approved_inapplicable_form(self):
        deal, library, session = self.fixture()
        session.print_item('dealer-form')
        vehicle = copy.deepcopy(deal.inputs.vehicle)
        vehicle.is_new = False
        deal.set_vehicle(vehicle)
        deal.certify()
        fresh = self.reload(deal, library)
        self.assertNotIn('dealer-form', fresh.items)
        self.assertEqual(fresh.documents[-1].doc_id, CHECKLIST_ID)
        self.assertIsNone(fresh.print_all()['failed'])
        self.assertEqual(len(self.provider.jobs_for('dealer-form')), 1)
        self.assertEqual(fresh.packaging_history[-1]['items']['dealer-form']['status'], 'printed')


class LeaseImagePackaging(ImagePackagingCases, LeasePackTestCase):
    pass
