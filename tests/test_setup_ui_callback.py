import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dealerpaperwork import setup_ui
from dealerpaperwork.setup_wizard import SetupWizard, SetupStep
from dealerpaperwork.users import UserStore


@unittest.skipUnless(setup_ui.TK_AVAILABLE, 'Tkinter import needed; no display/window is used')
class SetupCallbackTests(unittest.TestCase):
    def test_manager_single_admin_acknowledgment_reports_error_in_window(self):
        with tempfile.TemporaryDirectory() as folder:
            users = UserStore(Path(folder) / 'users.json')
            admin = users.create_user(actor=None, username='admin', display_name='Admin', role='admin', secret='pin')
            manager = users.create_user(actor=admin, username='manager', display_name='Manager', role='manager', secret='pin')
            wizard = SetupWizard(user_store=users, actor=manager)
            wizard.current_step = SetupStep.APPROVE
            window = object.__new__(setup_ui.SetupWizardWindow)
            window.wizard = wizard
            window.vars = {'approved_by': SimpleNamespace(get=lambda: 'Manager')}
            window.single_admin_var = SimpleNamespace(get=lambda: True)
            errors = []
            window._show_errors = errors.extend
            window._on_next()
            self.assertTrue(any('admin' in message.lower() for message in errors))
            self.assertIsNone(wizard.record)
