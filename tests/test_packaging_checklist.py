import importlib.util
from types import SimpleNamespace
from pathlib import Path
from test_print_pipeline import PrintPipelineTestCase
from dealerpaperwork.pack import build_pack, build_lease_pack, PackDocument, KIND_LIBRARY_DOC
from dealerpaperwork.print_pipeline import PrintPipelineError, PrintSession


class PackagingTests(PrintPipelineTestCase):
    def require_feature(self):
        self.assertIsNotNone(importlib.util.find_spec('dealerpaperwork.packaging_checklist'), 'generated checklist is missing')
        from dealerpaperwork.packaging_checklist import generate_checklist
        return generate_checklist

    def test_new_used_and_lease_lines_come_from_deal_and_pack(self):
        generate = self.require_feature()
        deal = self.make_deal()
        new = generate(deal, build_pack(deal))
        ids = {r.item_id for r in new}
        self.assertIn('lemon_law', ids)
        self.assertIn('payoff', ids)
        self.assertNotIn('buyers_guide', ids)
        deal.inputs.trade = None
        deal.inputs.vehicle.is_new = False
        used = {r.item_id for r in generate(deal, build_pack(deal))}
        self.assertIn('buyers_guide', used)
        self.assertNotIn('lemon_law', used)
        self.assertNotIn('payoff', used)
        lease = SimpleNamespace(deal_kind='lease', inputs=deal.inputs, preset=deal.preset)
        lease_ids = {r.item_id for r in generate(lease, build_lease_pack(lease))}
        self.assertIn('lease_payment', lease_ids)
        self.assertIn('lease_contract', lease_ids)
        extra = PackDocument('service_plan', 'SAMPLE service contract', 'standard_letter', kind=KIND_LIBRARY_DOC)
        rows = generate(deal, [*build_pack(deal), extra])
        self.assertEqual(sum(r.item_id == 'form:service_plan' for r in rows), 1)

    def test_jacket_library_forms_never_enter_lender_section(self):
        generate = self.require_feature()
        deal = self.make_deal(is_new=False)
        docs = [PackDocument(doc_id, doc_id, 'standard_letter', kind=KIND_LIBRARY_DOC)
                for doc_id in ('buyers_guide', 'privacy_notice')]
        rows = generate(deal, docs)
        for doc_id in ('buyers_guide', 'privacy_notice'):
            matching = [r for r in rows if r.source_doc_id == doc_id]
            self.assertEqual(len(matching), 1)
            self.assertTrue(matching[0].section.startswith('2.'))

    def test_checklist_last_and_completion_requires_named_checks(self):
        self.require_feature()
        ps = self.make_print_session(self.make_deal())
        self.finish_pack(ps)
        self.assertEqual(self.provider.jobs[-1].doc_id, 'paper_packaging_checklist')
        self.assertFalse(ps.is_complete)
        row = next(r for r in ps.packaging_rows if r.requires_paper)
        with self.assertRaises(PrintPipelineError):
            ps.check_packaging_item(row.item_id, actor='', reference='paper original inspected')
        with self.assertRaises(PrintPipelineError):
            ps.check_packaging_item(row.item_id, actor=self.fi)
        for row in ps.packaging_rows:
            ps.check_packaging_item(row.item_id, actor=self.fi, reference='SAMPLE paper original / secure form 001')
        self.assertTrue(ps.is_complete)
        events = [e for e in self.audit.read_events() if e.event_type == 'packaging_item_checked']
        self.assertEqual(len(events), len(ps.packaging_rows))
        self.assertTrue(all(e.actor == 'fay' and e.timestamp for e in events))
        saved = PrintSession.load(ps.deal_id, deal=ps.deal, setup=self.setup_path,
            print_config=ps.print_config, session_dir=self.sessions_dir,
            provider=self.provider, audit_log=self.audit, actor=self.fi)
        self.assertTrue(saved.is_complete)

    def test_void_supersedes_checklist_and_resets_checks(self):
        self.require_feature()
        ps = self.make_print_session(self.make_deal())
        self.finish_pack(ps)
        for row in ps.packaging_rows:
            ps.check_packaging_item(row.item_id, actor=self.fi, reference='SAMPLE paper original 001')
        self.assertTrue(ps.is_complete)
        ps.void_item('buyers_order', actor=self.fi, reason='spoiled page')
        self.assertFalse(ps.is_complete)
        self.assertEqual(ps.packaging_revision, 2)
        self.assertEqual(ps.packaging_checks, {})
        self.assertEqual(ps.item('paper_packaging_checklist').status, 'pending')
        ps.resume()
        self.assertEqual(self.provider.jobs[-1].doc_id, 'paper_packaging_checklist')
        self.assertIn('Checklist version: 2', self.provider.jobs[-1].payload)
        self.assertTrue(ps.packaging_history)

    def test_selected_checklist_cannot_print_before_pack_and_edit_invalidates_session(self):
        self.require_feature()
        ps = self.make_print_session(self.make_deal())
        with self.assertRaises(PrintPipelineError):
            ps.print_item('paper_packaging_checklist')
        ps.deal.set_money(selling_price='36000')
        with self.assertRaises(PrintPipelineError):
            ps.print_all()
        self.assertFalse(ps.is_complete)

    def test_render_includes_instructions_signoffs_and_deal_identity(self):
        self.require_feature()
        ps = self.make_print_session(self.make_deal())
        ps.print_all()
        text = self.provider.jobs[-1].payload
        for term in ('Sample Buyer', '1SAMPLEVIN0000000', 'no white-out', 'DMS exactly as signed', 'F&I manager', 'assembled', 'privacy', 'Checklist version: 1'):
            self.assertIn(term.lower(), text.lower())

    def test_other_open_deal_edit_prevents_old_clean_print(self):
        self.require_feature()
        from dealerpaperwork.deal_flow import DealSession
        deal = self.make_deal()
        ps = self.make_print_session(deal)
        separate = DealSession.load(deal.deal_id, user=self.fi, preset=self.preset, deals_dir=self.deals_dir, audit_log=self.audit)
        separate.set_money(selling_price='36000')
        with self.assertRaises(PrintPipelineError):
            ps.print_all()
        self.assertEqual(self.provider.jobs, [])
