"""UI-independent identity → disclaimer → setup/home launch sequence."""
from __future__ import annotations
import json
from pathlib import Path
from .audit import AuditLog
from .disclaimer import DisclaimerStore, VERSION
from .setup_wizard import is_setup_complete
from .users import UserStore


class StartupFlow:
    def __init__(self, data_root=Path('.'), *, disclaimer_version=VERSION,
                 setup_path=None, users_path=None, audit_path=None):
        self.root = Path(data_root)
        self.setup_path = Path(setup_path) if setup_path is not None else self.root / 'config/setup.json'
        self.audit = AuditLog(audit_path if audit_path is not None else self.root / 'data/audit.jsonl')
        self.users = UserStore(users_path if users_path is not None else self.root / 'data/users.json', audit_log=self.audit)
        self.disclaimer = DisclaimerStore(self.root, audit_log=self.audit, version=disclaimer_version)
        self.user = None
        self.pending_path = self.root / 'data/bootstrap_pending.json'

    @property
    def stage(self):
        if self.users.is_empty:
            return 'bootstrap'
        if self.user is None:
            return 'sign_in'
        if not self.disclaimer.has_accepted(self.user):
            return 'disclaimer'
        return 'home' if is_setup_complete(self.setup_path) else 'setup'

    def bootstrap(self, *, username, display_name, secret, confirmation):
        if not self.users.is_empty:
            raise PermissionError('The first admin already exists. Sign in instead.')
        # Lane's ruling: acceptance is the first audit entry on a fresh install.
        # Persist identity now; preserve its original creation time for later audit.
        self.users.audit_log = None
        try:
            self.user = self.users.create_confirmed_user(
                actor=None, username=username, display_name=display_name,
                role='admin', secret=secret, confirmation=confirmation)
        finally:
            self.users.audit_log = self.audit
        self.pending_path.write_text(json.dumps({
            'username': self.user.username, 'role': self.user.role,
            'display_name': self.user.display_name, 'created_at': self.user.created_at,
            'account_id': self.user.account_id,
        }), encoding='utf-8')
        return self.user

    def sign_in(self, username, secret):
        audit_exists = bool(self.audit.read_events())
        self.users.audit_log = self.audit if audit_exists else None
        try:
            self.user = self.users.authenticate(username, secret)
        finally:
            self.users.audit_log = self.audit
        return self.user

    def accept(self):
        if self.user is None:
            raise PermissionError('Sign in before accepting the notice.')
        event = self.disclaimer.accept(self.user)
        if self.pending_path.exists():
            pending = json.loads(self.pending_path.read_text(encoding='utf-8'))
            self.audit.record('user_created', 'first-run bootstrap', {
                **pending, 'recorded_after_disclaimer': True})
            self.pending_path.unlink()
        return event

    def require_access(self):
        if self.user is None or not self.disclaimer.has_accepted(self.user):
            raise PermissionError('Sign in and accept the current notice before using the app.')
        return self.user
