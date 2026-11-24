from pathlib import Path
from test_setup_wizard import WizardTestCase
from dealerpaperwork.audit import AuditLog
from dealerpaperwork.users import UserStore
from dealerpaperwork.setup_wizard import SetupIncompleteError


class SetupRecoveryTests(WizardTestCase):
    def setUp(self):
        super().setUp()
        self.audit = AuditLog(Path(self.tmp.name) / 'audit.jsonl')
        self.users = UserStore(Path(self.tmp.name) / 'users.json', audit_log=self.audit)
        self.admin = self.users.create_user(actor=None, username='admin', display_name='Admin Person', role='admin', secret='pin')
        self.wizard.user_store = self.users
        self.wizard.actor = self.admin
        self.wizard.audit_log = self.audit

    def test_single_admin_requires_named_acknowledgment_and_persists_it(self):
        self.assertTrue(hasattr(self.wizard, 'needs_single_admin_ack'), 'single-admin guidance is missing')
        self.assertTrue(self.wizard.needs_single_admin_ack)
        self.drive_to_approve()
        from decimal import Decimal
        self.wizard.set_tax_data(county_surtax_rates={'Sample County': Decimal('0.015')})
        self.wizard.approve('Admin Person')
        with self.assertRaises(SetupIncompleteError):
            self.wizard.finish()
        self.wizard.acknowledge_single_admin(self.admin)
        self.wizard.finish()
        import json
        data = json.loads(self.setup_path.read_text())
        self.assertEqual(data['single_admin_ack']['username'], 'admin')
        events = [e for e in self.audit.read_events() if e.event_type == 'single_admin_acknowledged']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor, 'admin')
        self.assertTrue(events[0].timestamp)

    def test_two_admins_finish_without_warning_or_acknowledgment(self):
        self.users.create_user(actor=self.admin, username='second', display_name='Second', role='admin', secret='second')
        self.assertTrue(hasattr(self.wizard, 'needs_single_admin_ack'), 'single-admin guidance is missing')
        self.assertFalse(self.wizard.needs_single_admin_ack)
        self.finish_happy_path()
        self.assertFalse(any(e.event_type == 'single_admin_acknowledged' for e in self.audit.read_events()))
