import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dealerpaperwork.audit import AuditLog
from dealerpaperwork.users import UserStore, UserError, AuthenticationError, PermissionDeniedError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.audit = AuditLog(self.base / 'audit.jsonl')
        self.store = UserStore(self.base / 'users.json', audit_log=self.audit)
        self.admin = self.create(None, 'admin', 'admin', 'initial-secret')

    def create(self, actor, name, role, secret):
        return self.store.create_user(actor=actor, username=name, display_name=name.title(), role=role, secret=secret)

    def test_admin_reset_rotates_verifier_persists_and_preserves_identity(self):
        target = self.create(self.admin, 'other', 'admin', 'old-target-secret')
        old_salt = target.salt_hex
        self.assertTrue(callable(getattr(self.store, 'reset_secret', None)), 'account recovery is missing')
        self.store.reset_secret(actor=self.admin, username='OTHER', secret='new-target-secret', confirmation='new-target-secret')
        reloaded = UserStore(self.store.path)
        with self.assertRaises(AuthenticationError):
            reloaded.authenticate('other', 'old-target-secret')
        actual = reloaded.authenticate('other', 'new-target-secret')
        self.assertNotEqual(actual.salt_hex, old_salt)
        self.assertEqual((actual.username, actual.role, actual.created_at), ('other', 'admin', target.created_at))
        event = self.audit.read_events()[-1]
        self.assertEqual((event.event_type, event.actor, event.details['target']), ('pin_reset', 'admin', 'other'))
        for secret in ('initial-secret', 'old-target-secret', 'new-target-secret'):
            self.assertNotIn(secret, self.audit.path.read_text() + self.store.path.read_text())

    def test_other_roles_cannot_reset_and_mismatch_does_not_change_secret(self):
        self.assertTrue(callable(getattr(self.store, 'reset_secret', None)), 'account recovery is missing')
        for role in ('manager', 'fi', 'sales'):
            actor = self.create(self.admin, role, role, 'role-secret')
            with self.assertRaises(PermissionDeniedError):
                self.store.reset_secret(actor=actor, username='admin', secret='replacement', confirmation='replacement')
        with self.assertRaises(UserError):
            self.store.reset_secret(actor=self.admin, username='admin', secret='replacement', confirmation='typo')
        self.store.authenticate('admin', 'initial-secret')

    def test_self_change_requires_current_secret_and_audits(self):
        self.assertTrue(callable(getattr(self.store, 'change_secret', None)), 'self-service change is missing')
        actor = self.create(self.admin, 'sales', 'sales', 'old-self-secret')
        with self.assertRaises(AuthenticationError):
            self.store.change_secret(actor=actor, current_secret='wrong', secret='new-self-secret', confirmation='new-self-secret')
        self.store.change_secret(actor=actor, current_secret='old-self-secret', secret='new-self-secret', confirmation='new-self-secret')
        with self.assertRaises(AuthenticationError):
            self.store.authenticate('sales', 'old-self-secret')
        self.store.authenticate('sales', 'new-self-secret')
        events = [e for e in self.audit.read_events() if e.event_type == 'pin_changed']
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].actor, events[0].details['target']), ('sales', 'sales'))

    def test_confirmed_creation_mismatch_creates_nothing(self):
        self.assertTrue(callable(getattr(self.store, 'create_confirmed_user', None)), 'confirmed creation is missing')
        with self.assertRaises(UserError):
            self.store.create_confirmed_user(actor=self.admin, username='typo', display_name='Typo', role='sales', secret='intended', confirmation='mistyped')
        self.assertIsNone(self.store.get_user('typo'))
        created = self.store.create_confirmed_user(actor=self.admin, username='right', display_name='Right', role='sales', secret='intended', confirmation='intended')
        self.assertEqual(self.store.authenticate('right', 'intended').username, created.username)

    def test_missing_target_and_blank_secret_are_refused(self):
        self.assertTrue(callable(getattr(self.store, 'reset_secret', None)), 'account recovery is missing')
        for name, secret in [('missing', 'replacement'), ('admin', '')]:
            with self.assertRaises(UserError):
                self.store.reset_secret(actor=self.admin, username=name, secret=secret, confirmation=secret)

    def test_another_open_store_cannot_authenticate_or_restore_revoked_pin(self):
        target = self.create(self.admin, 'target', 'sales', 'old-target-secret')
        stale = UserStore(self.store.path, audit_log=self.audit)
        self.store.reset_secret(actor=self.admin, username=target.username, secret='replacement', confirmation='replacement')
        with self.assertRaises(AuthenticationError):
            stale.authenticate('target', 'old-target-secret')
        stale.create_user(actor=self.admin, username='unrelated', display_name='Unrelated', role='sales', secret='another')
        self.assertEqual(UserStore(self.store.path).authenticate('target', 'replacement').username, 'target')

    def test_corrupt_user_store_is_not_treated_as_first_run(self):
        self.store.path.write_text('{')
        with self.assertRaises(UserError):
            UserStore(self.store.path)


if __name__ == '__main__':
    unittest.main()
