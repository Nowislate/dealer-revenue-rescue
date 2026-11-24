from test_print_pipeline import PrintPipelineTestCase
from dealerpaperwork.deal_flow import DealSession


class PackagingDetailsTests(PrintPipelineTestCase):
    def test_entered_lender_persists_and_edit_requires_recertification(self):
        deal = self.make_deal()
        self.assertTrue(callable(getattr(deal, 'set_packaging_details', None)), 'entered packaging details are missing')
        before = deal.worksheet_result.total_tax
        deal.set_packaging_details(transaction_type='finance', lender_name='SAMPLE Lender')
        self.assertFalse(deal.is_certified)
        deal.certify()
        self.assertEqual(deal.worksheet_result.total_tax, before)
        loaded = DealSession.load(deal.deal_id, user=self.fi, preset=self.preset, deals_dir=self.deals_dir, audit_log=self.audit)
        self.assertEqual(loaded.packaging_details['lender_name'], 'SAMPLE Lender')

    def test_cash_pack_omits_finance_only_lines(self):
        deal = self.make_deal()
        self.assertTrue(callable(getattr(deal, 'set_packaging_details', None)), 'entered packaging details are missing')
        deal.set_packaging_details(transaction_type='cash', lender_name='')
        deal.certify()
        ps = self.make_print_session(deal)
        ids = {row.item_id for row in ps.packaging_rows}
        self.assertNotIn('contract', ids)
        self.assertNotIn('insurance', ids)
        self.assertNotIn('retail_installment_contract', [doc.doc_id for doc in ps.documents])
