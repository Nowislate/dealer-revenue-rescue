import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dealerpaperwork.users import UserError


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.assertIsNotNone(importlib.util.find_spec('dealerpaperwork.startup'), 'shared startup gate is missing')
        self.module = importlib.import_module('dealerpaperwork.startup')
        self.flow = self.module.StartupFlow(self.base)

    def bootstrap(self):
        return self.flow.bootstrap(username='admin', display_name='Admin', secret='pin', confirmation='pin')

    def test_bootstrap_identity_only_and_acceptance_is_first_audit_event(self):
        self.assertEqual(self.flow.stage, 'bootstrap')
        with self.assertRaises(PermissionError):
            self.flow.require_access()
        self.bootstrap()
        self.assertEqual(self.flow.stage, 'disclaimer')
        self.assertEqual(self.flow.audit.read_events(), [])
        self.assertFalse((self.base / 'config/setup.json').exists())
        with self.assertRaises(PermissionError):
            self.flow.require_access()
        self.flow.accept()
        self.assertEqual(self.flow.audit.read_events()[0].event_type, 'disclaimer_accepted')
        self.assertEqual(self.flow.stage, 'setup')
        self.assertEqual(self.flow.require_access().username, 'admin')

    def test_restart_between_bootstrap_and_acceptance_still_logs_acceptance_first(self):
        self.bootstrap()
        restarted = self.module.StartupFlow(self.base)
        restarted.sign_in('admin', 'pin')
        self.assertEqual(restarted.audit.read_events(), [])
        restarted.accept()
        self.assertEqual(restarted.audit.read_events()[0].event_type, 'disclaimer_accepted')

    def test_existing_user_signin_version_gate_and_reject_do_not_touch_deals(self):
        self.bootstrap()
        self.flow.accept()
        deal = self.base / 'data/deals/autosaved.json'
        deal.parent.mkdir(parents=True)
        deal.write_bytes(b'{"unchanged":true}')
        existing = self.module.StartupFlow(self.base, disclaimer_version='new')
        self.assertEqual(existing.stage, 'sign_in')
        existing.sign_in('admin', 'pin')
        self.assertEqual(existing.stage, 'disclaimer')
        with self.assertRaises(PermissionError):
            existing.require_access()
        self.assertEqual(deal.read_bytes(), b'{"unchanged":true}')
        existing.accept()
        self.assertEqual(existing.stage, 'setup')

    def test_account_creation_confirmation_and_each_user_accepts(self):
        with self.assertRaises(UserError):
            self.flow.bootstrap(username='bad', display_name='Bad', secret='pin', confirmation='oops')
        self.assertTrue(self.flow.users.is_empty)
        self.bootstrap()
        self.flow.accept()
        admin = self.flow.require_access()
        self.flow.users.create_confirmed_user(actor=admin, username='second', display_name='Second', role='sales', secret='second', confirmation='second')
        other = self.module.StartupFlow(self.base)
        other.sign_in('second', 'second')
        self.assertEqual(other.stage, 'disclaimer')
        other.accept()
        self.assertEqual(other.require_access().username, 'second')


if __name__ == '__main__':
    unittest.main()
