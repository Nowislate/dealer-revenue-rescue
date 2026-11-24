import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dealerpaperwork.audit import AuditLog
from dealerpaperwork.backup import create_backup, restore_backup
from dealerpaperwork.users import UserStore


class DisclaimerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / 'app'
        self.audit = AuditLog(self.base / 'data/audit.jsonl')
        self.users = UserStore(self.base / 'data/users.json', audit_log=self.audit)
        self.admin = self.users.create_user(actor=None, username='admin', display_name='Admin Person', role='admin', secret='secret')
        self.assertIsNotNone(importlib.util.find_spec('dealerpaperwork.disclaimer'), 'disclaimer gate is missing')
        self.module = importlib.import_module('dealerpaperwork.disclaimer')
        self.gate = self.module.DisclaimerStore(self.base, audit_log=self.audit)

    def test_per_account_version_and_persistence(self):
        self.assertFalse(self.gate.has_accepted(self.admin))
        first = self.gate.accept(self.admin)
        self.assertTrue(first.timestamp)
        again = self.module.DisclaimerStore(self.base, audit_log=self.audit)
        self.assertTrue(again.has_accepted(self.admin))
        again.accept(self.admin)
        events = [e for e in self.audit.read_events() if e.event_type == 'disclaimer_accepted']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor, 'admin')
        self.assertEqual(events[0].details['role'], 'admin')
        self.assertEqual(events[0].details['version'], self.module.VERSION)
        changed = self.module.DisclaimerStore(self.base, audit_log=self.audit, version='next')
        self.assertFalse(changed.has_accepted(self.admin))

    def test_different_user_requires_acceptance_and_secret_reset_retains_it(self):
        self.gate.accept(self.admin)
        other = self.users.create_user(actor=self.admin, username='other', display_name='Other', role='sales', secret='other')
        self.assertFalse(self.gate.has_accepted(other))
        self.users.reset_secret(actor=self.admin, username='admin', secret='new-secret', confirmation='new-secret')
        self.assertTrue(self.gate.has_accepted(UserStore(self.users.path).get_user('admin')))

    def test_same_username_after_it_recovery_is_a_new_identity(self):
        self.gate.accept(self.admin)
        self.users.path.unlink()
        recreated = UserStore(self.users.path).create_user(actor=None, username='admin', display_name='Admin Person', role='admin', secret='different')
        self.assertFalse(self.gate.has_accepted(recreated))

    def test_restore_keeps_same_install_but_new_machine_asks_again(self):
        self.gate.accept(self.admin)
        backup = create_backup(self.base, Path(self.tmp.name) / 'backups', 'acceptance')
        # Restore into a different root models a new installation; no machine id travels.
        new_root = Path(self.tmp.name) / 'new-machine'
        restore_backup(backup, new_root)
        restored_user = UserStore(new_root / 'data/users.json').get_user('admin')
        new_gate = self.module.DisclaimerStore(new_root, audit_log=AuditLog(new_root / 'data/audit.jsonl'))
        self.assertFalse(new_gate.has_accepted(restored_user))
        import zipfile
        with zipfile.ZipFile(backup) as z:
            self.assertIn('data/disclaimer_acceptances.json', z.namelist())
            self.assertNotIn('data/installation.json', z.namelist())
        # Restore on the same machine preserves its local id.
        restore_backup(backup, self.base)
        self.assertTrue(self.module.DisclaimerStore(self.base, audit_log=self.audit).has_accepted(self.admin))

    def test_acceptance_never_writes_deals_and_requires_a_named_account(self):
        deal = self.base / 'data/deals/saved.json'
        deal.parent.mkdir(parents=True)
        deal.write_bytes(b'{"autosaved": true}')
        self.gate.accept(self.admin)
        self.assertEqual(deal.read_bytes(), b'{"autosaved": true}')
        with self.assertRaises(ValueError):
            self.gate.accept(None)

    def test_draft_text_is_fixed_and_not_a_dealer_preset(self):
        self.assertIn('This software ships with no forms.', self.module.TEXT)
        self.assertIn('Apache License, Version 2.0', self.module.TEXT)
        self.assertIn('annual percentage rate', self.module.TEXT)
        self.assertTrue(self.module.VERSION)


if __name__ == '__main__':
    unittest.main()
