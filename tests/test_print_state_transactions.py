import json
from unittest.mock import patch
from test_print_pipeline import PrintPipelineTestCase
from dealerpaperwork.deal_flow import DealSession
from dealerpaperwork.print_pipeline import PrintSession, PrintPipelineError


class PrintStateTransactionTests(PrintPipelineTestCase):
    def completed(self):
        deal = self.make_deal()
        ps = self.make_print_session(deal)
        self.finish_pack(ps)
        self.finish_packaging(ps)
        self.sales = self.store.create_user(actor=self.admin, username='sales', display_name='Sales', role='sales', secret='sales-pin')
        return deal, ps

    def load_session(self, deal, actor=None):
        actor = actor or self.fi
        loaded = DealSession.load(deal.deal_id, user=actor, preset=self.preset, deals_dir=self.deals_dir, audit_log=self.audit)
        return PrintSession.load(deal.deal_id, deal=loaded, setup=self.setup_path,
            print_config=self.make_config, session_dir=self.sessions_dir,
            provider=self.provider, audit_log=self.audit, actor=actor)

    def test_stale_sales_cannot_restore_voided_approval_through_any_mutator(self):
        deal, current = self.completed()
        self.make_config = current.print_config
        operations = ('pull', 'save', 'print', 'all', 'resume', 'check', 'void', 'reprint')
        stale = [self.load_session(deal, self.sales) for _ in operations]
        current.void_item('buyers_order', actor=self.fi, reason='spoiled')
        expected = current.file_path.read_bytes()
        audit_count, job_count = len(self.audit.read_events()), len(self.provider.jobs)
        for name, session in zip(operations, stale):
            with self.subTest(operation=name):
                with self.assertRaises(PrintPipelineError):
                    if name == 'pull': session.pull_secure_item('secure_poa')
                    elif name == 'save': session.save()
                    elif name == 'print': session.print_item('buyers_order')
                    elif name == 'all': session.print_all()
                    elif name == 'resume': session.resume()
                    elif name == 'check': session.check_packaging_item('buyer_copies')
                    elif name == 'void': session.void_item('buyers_order', reason='stale')
                    elif name == 'reprint': session.reprint_item('buyers_order')
                self.assertEqual(current.file_path.read_bytes(), expected)
                self.assertEqual(len(self.audit.read_events()), audit_count)
                self.assertEqual(len(self.provider.jobs), job_count)
        self.assertFalse(stale[0].is_complete)
        fresh = self.load_session(deal)
        self.assertEqual(fresh.packaging_revision, 2)
        self.assertEqual(fresh.packaging_checks, {})
        fresh.resume()
        self.finish_packaging(fresh)
        self.assertTrue(fresh.is_complete)

    def test_load_never_publishes_empty_checks_over_saved_approval(self):
        deal, current = self.completed()
        self.make_config = current.print_config
        before = current.file_path.read_bytes()
        observed = []
        restore = PrintSession._restore
        def inspect_restoration(session, data):
            observed.append(json.loads(session.file_path.read_text())['packaging_checks'])
            return restore(session, data)
        with patch.object(PrintSession, '_restore', inspect_restoration):
            loaded = self.load_session(deal)
        self.assertTrue(observed)
        self.assertTrue(all('fi_signoff' in checks for checks in observed))
        self.assertEqual(current.file_path.read_bytes(), before)
        self.assertTrue(loaded.is_complete)

    def test_missing_or_corrupt_state_is_not_resurrected_by_cached_save(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                deal = self.make_deal()
                ps = self.make_print_session(deal)
                if corrupt: ps.file_path.write_text('{')
                else: ps.file_path.unlink()
                with self.assertRaises(PrintPipelineError): ps.save()
                self.assertFalse(ps.is_complete)
                if corrupt: self.assertEqual(ps.file_path.read_text(), '{')
                else: self.assertFalse(ps.file_path.exists())

    def test_legacy_state_migrates_once_without_losing_approved_checks(self):
        deal, current = self.completed()
        self.make_config = current.print_config
        data = json.loads(current.file_path.read_text())
        data.pop('state_version', None)
        current.file_path.write_text(json.dumps(data))
        loaded = self.load_session(deal)
        self.assertTrue(loaded.is_complete)
        version = json.loads(loaded.file_path.read_text()).get('state_version')
        self.assertIsInstance(version, int)
        before = loaded.file_path.read_bytes()
        self.assertTrue(self.load_session(deal).is_complete)
        self.assertEqual(loaded.file_path.read_bytes(), before)

    def test_custom_export_cannot_bypass_stale_canonical_guard(self):
        deal, current = self.completed()
        self.make_config = current.print_config
        stale = self.load_session(deal, self.sales)
        current.void_item('buyers_order', reason='spoiled')
        exported = self.sessions_dir / 'export.json'
        with self.assertRaises(PrintPipelineError): stale.save(exported)
        self.assertFalse(exported.exists())

    def test_exported_branch_cannot_overwrite_another_live_session(self):
        deal, current = self.completed()
        self.make_config = current.print_config
        export_dir = self.sessions_dir / 'exports'
        exported = export_dir / (deal.deal_id + '.json')
        current.save(exported)
        exported_session = PrintSession.load(deal.deal_id, deal=deal, setup=self.setup_path,
            print_config=current.print_config, session_dir=export_dir,
            provider=self.provider, audit_log=self.audit, actor=self.sales)
        current.void_item('buyers_order', reason='spoiled')
        before = current.file_path.read_bytes()
        with self.assertRaises(PrintPipelineError): exported_session.save(current.file_path)
        self.assertEqual(current.file_path.read_bytes(), before)
        self.assertEqual(json.loads(before)['packaging_revision'], 2)
        self.assertFalse(self.load_session(deal).is_complete)

    def test_fresh_export_is_an_immutable_snapshot_and_keeps_source_current(self):
        deal, current = self.completed()
        exported = self.sessions_dir / 'export-v1.json'
        current.save(exported)
        before = exported.read_bytes()
        self.assertTrue(current.is_complete)
        with self.assertRaises(PrintPipelineError): current.save(exported)
        self.assertEqual(exported.read_bytes(), before)
