import unittest
from unittest.mock import patch
from test_print_pipeline import PrintPipelineTestCase
from dealerpaperwork import print_ui
from dealerpaperwork.print_pipeline import PrintSession


@unittest.skipUnless(print_ui.TK_AVAILABLE, 'Tk import needed; no native window is created')
class PrintCallbackTests(PrintPipelineTestCase):
    def test_print_all_and_resume_show_stale_recovery_message(self):
        deal = self.make_deal()
        current = self.make_print_session(deal)
        self.finish_pack(current)
        self.finish_packaging(current)
        stale = PrintSession.load(deal.deal_id, deal=deal, setup=self.setup_path,
            print_config=current.print_config, session_dir=self.sessions_dir,
            provider=self.provider, audit_log=self.audit, actor=self.fi)
        current.void_item('buyers_order', reason='spoiled')
        window = object.__new__(print_ui.PrintWindow)
        window.session = stale
        refreshed = []
        window.refresh = lambda: refreshed.append(True)
        with patch.object(print_ui.messagebox, 'showerror') as message:
            window._print_all()
            window._resume()
        self.assertEqual(message.call_count, 2)
        self.assertEqual(len(refreshed), 2)
        self.assertTrue(all('reopen' in str(call).lower() for call in message.call_args_list))
