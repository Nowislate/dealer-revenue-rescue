"""Cross-version saved states authored by the actual pre-image baseline."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dealerpaperwork.deal_flow import DealSession, preset_from_setup_file
from dealerpaperwork.lease_flow import LeaseDealSession
from dealerpaperwork.doc_library import DocumentLibrary, FieldPlacement
from dealerpaperwork.print_config import PrintConfig
from dealerpaperwork.print_pipeline import PrintSession
from dealerpaperwork.printers import FakePrinterProvider
from dealerpaperwork.users import UserStore

FIX = Path(__file__).parent / 'fixtures'
SAVED = json.loads((FIX / 'legacy_sessions-v1.json').read_text())['fixtures']


class LegacySessionMigration(unittest.TestCase):
    def restore_fixture(self, kind, state, *, pr4=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        data = copy.deepcopy(SAVED[kind])
        (root / 'setup.json').write_text(json.dumps(data['setup']))
        for folder in ('deals', 'documents', 'sessions'):
            (root / folder).mkdir()
        deal_id = data['deal']['deal_id']
        (root / 'deals' / (deal_id + '.json')).write_text(json.dumps(data['deal']))
        (root / 'documents/library.json').write_text(json.dumps(data['library']))
        for name, content in data['source_files'].items():
            (root / 'documents' / name).write_bytes(content.encode())
        saved = copy.deepcopy(data['states'][state])
        if pr4:
            saved['packaging_fingerprint'] = data['pr4_fingerprint']
            saved['document_order'] = list(saved['items'])
        (root / 'sessions' / (deal_id + '.json')).write_text(json.dumps(saved))
        users = UserStore(root / 'users.json')
        admin = users.create_user(actor=None, username='ada', display_name='Ada Admin', role='admin', secret='sample-admin')
        user = users.create_user(actor=admin, username='fay', display_name='Fay F&I', role='fi', secret='sample-fi')
        cls = LeaseDealSession if kind == 'lease' else DealSession
        deal = cls.load(deal_id, user=user, preset=preset_from_setup_file(root / 'setup.json'), deals_dir=root / 'deals')
        library = DocumentLibrary(root / 'documents')
        provider = FakePrinterProvider()
        def load():
            return PrintSession.load(deal_id, deal=deal, setup=root / 'setup.json',
                print_config=PrintConfig(path=root / 'print_config.json'), session_dir=root / 'sessions',
                provider=provider, actor=user, library=DocumentLibrary(library.library_dir))
        return saved, deal, library, provider, admin, load

    def assert_preserved(self, saved, session):
        self.assertEqual(session.packaging_revision, saved['packaging_revision'])
        self.assertEqual(session.packaging_checks, saved['packaging_checks'])
        self.assertEqual(session.packaging_history, saved['packaging_history'])
        for doc_id, row in saved['items'].items():
            current = session.item(doc_id)
            for key in ('status', 'job', 'print_count', 'printed_at', 'reprint_pending', 'pulled_by'):
                self.assertEqual(getattr(current, key), row[key], (doc_id, key))

    def test_baseline_partial_and_complete_sessions_preserve_submissions(self):
        for kind in SAVED:
            for state in ('partial', 'complete'):
                with self.subTest(kind=kind, state=state):
                    saved, _, _, provider, _, load = self.restore_fixture(kind, state)
                    session = load()
                    self.assert_preserved(saved, session)
                    session.resume()
                    self.assertEqual(provider.jobs_for('legacy-form'), [])
                    if state == 'complete':
                        self.assertTrue(session.is_complete)
                        self.assertEqual(provider.jobs, [])

    def test_initial_pr4_expanded_legacy_fingerprints_also_preserve_state(self):
        for kind in SAVED:
            with self.subTest(kind=kind):
                saved, _, _, provider, _, load = self.restore_fixture(kind, 'complete', pr4=True)
                session = load()
                self.assert_preserved(saved, session)
                session.resume()
                self.assertTrue(session.is_complete)
                self.assertEqual(provider.jobs, [])

    def test_real_mapping_or_image_changes_still_supersede_old_approval(self):
        for kind in SAVED:
            for change in ('mapping', 'image'):
                with self.subTest(kind=kind, change=change):
                    saved, _, library, _, admin, load = self.restore_fixture(kind, 'complete')
                    if change == 'mapping':
                        library.update_placements('legacy-form', [FieldPlacement('buyer_name', 1, 35, 55)])
                    else:
                        library.replace_page_images('legacy-form', [FIX / 'form_images/page-1.jpg'])
                    library.approve_document('legacy-form', admin)
                    session = load()
                    self.assertEqual(session.packaging_revision, saved['packaging_revision'] + 1)
                    self.assertEqual(session.packaging_checks, {})
                    self.assertFalse(session.is_complete)
                    self.assertEqual(session.item('legacy-form').status, 'pending')
