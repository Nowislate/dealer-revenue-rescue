"""Local user accounts and role-based access control (Stage 3).

Accounts live in a local JSON file on the store's machine -- no server,
no network, no cloud identity (locked decisions 5 and 7: the dealer's
IT team owns accounts; the app just enforces the roles locally).

A user's secret (PIN or password) is NEVER stored in plaintext: each
account stores a per-user random salt and a PBKDF2-HMAC-SHA256 derived
key (``hashlib.pbkdf2_hmac``). Authentication re-derives the key and
compares in constant time.

Roles and the permission matrix (spec Sections 7-8):

    admin    manage users, approve presets, certify deals, edit deals
    manager  approve presets, certify deals, edit deals
    fi       certify deals, edit deals
    sales    edit deals ONLY (cannot certify, cannot approve presets)

First-run rule: when no users exist yet, creating the first account is
allowed without an authenticated actor, and that account must be an
admin (otherwise nobody could ever manage users). After that,
``create_user`` requires an actor who holds ``manage_users`` -- in
practice, an admin.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import os
import threading
from contextlib import contextmanager
from functools import wraps
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .audit import (
    EVENT_LOGIN_FAILED,
    EVENT_LOGIN_SUCCEEDED,
    EVENT_USER_CREATED,
    AuditLog,
)

DEFAULT_USERS_PATH = Path("data") / "users.json"

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_FI = "fi"
ROLE_SALES = "sales"
ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_FI, ROLE_SALES)

ACTION_MANAGE_USERS = "manage_users"
ACTION_APPROVE_PRESET = "approve_preset"
ACTION_CERTIFY_DEAL = "certify_deal"
ACTION_EDIT_DEAL = "edit_deal"
ACTIONS = (
    ACTION_MANAGE_USERS,
    ACTION_APPROVE_PRESET,
    ACTION_CERTIFY_DEAL,
    ACTION_EDIT_DEAL,
)

# The permission matrix. Module-level and plainly readable on purpose:
# this table IS the RBAC policy for the app.
PERMISSIONS: dict[str, frozenset] = {
    ROLE_ADMIN: frozenset(ACTIONS),
    ROLE_MANAGER: frozenset(
        (ACTION_APPROVE_PRESET, ACTION_CERTIFY_DEAL, ACTION_EDIT_DEAL)
    ),
    ROLE_FI: frozenset((ACTION_CERTIFY_DEAL, ACTION_EDIT_DEAL)),
    ROLE_SALES: frozenset((ACTION_EDIT_DEAL,)),
}

_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def has_permission(user: "User", action: str) -> bool:
    """True when ``user``'s role grants ``action`` (see PERMISSIONS)."""
    if action not in ACTIONS:
        raise ValueError(
            f"Unknown action {action!r}; expected one of {', '.join(ACTIONS)}."
        )
    return action in PERMISSIONS.get(user.role, frozenset())


class UserError(Exception):
    """Base class for account-store failures."""


class PermissionDeniedError(UserError):
    """The acting user does not hold the permission the action needs."""


class DuplicateUserError(UserError):
    """A username is already taken."""


class AuthenticationError(UserError):
    """Username/secret pair did not authenticate. The message is the
    same for an unknown user and a wrong secret, on purpose."""


@dataclass
class User:
    """One local account. ``salt_hex``/``key_hex`` are the password
    verifier; the secret itself is never stored anywhere."""

    username: str
    display_name: str
    role: str
    salt_hex: str
    key_hex: str
    iterations: int
    created_at: str
    account_id: str = ""

    def has_permission(self, action: str) -> bool:
        return has_permission(self, action)


def _derive_key(secret: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), salt, iterations
    )


def _fresh_accounts(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._transaction():
            return method(self, *args, **kwargs)
    return call


class UserStore:
    """A JSON-file-backed store of local user accounts."""

    def __init__(
        self,
        path: Union[str, Path] = DEFAULT_USERS_PATH,
        *,
        audit_log: Optional[AuditLog] = None,
    ) -> None:
        self.path = Path(path)
        self.audit_log = audit_log
        self._mutex = threading.RLock()
        self._lock_depth = 0
        self._users: dict[str, User] = {}
        self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._users = {}
            return
        except (OSError, ValueError) as exc:
            raise UserError('The account file cannot be read. Ask dealer IT to recover it; it was not reset.') from exc
        if not isinstance(raw, dict) or not isinstance(raw.get('users'), list):
            raise UserError('The account file is malformed. Ask dealer IT to recover it.')
        self._users = {}
        for entry in raw.get("users", []):
            user = User(
                username=entry["username"],
                display_name=entry.get("display_name", entry["username"]),
                role=entry["role"],
                salt_hex=entry["salt_hex"],
                key_hex=entry["key_hex"],
                iterations=int(entry.get("iterations", _PBKDF2_ITERATIONS)),
                created_at=entry.get("created_at", ""),
                account_id=entry.get("account_id") or hashlib.sha256(
                    (entry["username"].lower() + entry["salt_hex"]).encode("utf-8")
                ).hexdigest(),
            )
            self._users[user.username.lower()] = user

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "users": [
                {
                    "username": u.username,
                    "display_name": u.display_name,
                    "role": u.role,
                    "salt_hex": u.salt_hex,
                    "key_hex": u.key_hex,
                    "iterations": u.iterations,
                    "created_at": u.created_at,
                    "account_id": u.account_id,
                }
                for u in self._users.values()
            ]
        }
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.path)

    @contextmanager
    def _transaction(self):
        """Serialize local account updates across native app instances."""
        with self._mutex:
            if self._lock_depth:
                yield
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix('.lock').open('a+b') as lock:
                if lock.tell() == 0:
                    lock.write(b'\0')
                    lock.flush()
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    self._load()
                    yield
                finally:
                    self._lock_depth = 0
                    lock.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock, fcntl.LOCK_UN)

    # -- queries ------------------------------------------------------------

    @property
    @_fresh_accounts
    def is_empty(self) -> bool:
        """True when no account exists yet (first run)."""
        return not self._users

    @_fresh_accounts
    def usernames(self) -> list[str]:
        return [u.username for u in self._users.values()]

    @_fresh_accounts
    def get_user(self, username: str) -> Optional[User]:
        return self._users.get((username or "").strip().lower())

    def has_permission(self, user: User, action: str) -> bool:
        return has_permission(user, action)

    # -- account creation -----------------------------------------------------

    @_fresh_accounts
    def create_user(
        self,
        *,
        actor: Optional[User],
        username: str,
        display_name: str,
        role: str,
        secret: str,
    ) -> User:
        """Create an account.

        First run (empty store): ``actor`` may be None and the new
        account must be an admin. Afterwards ``actor`` must hold the
        ``manage_users`` permission.
        """
        username = (username or "").strip()
        display_name = (display_name or "").strip() or username
        if not username:
            raise UserError("Username is blank.")
        if role not in ROLES:
            raise UserError(
                f"Unknown role {role!r}; expected one of {', '.join(ROLES)}."
            )
        if not secret:
            raise UserError("The PIN/password is blank.")
        if username.lower() in self._users:
            raise DuplicateUserError(f"Username {username!r} is already taken.")

        if self.is_empty:
            if role != ROLE_ADMIN:
                raise PermissionDeniedError(
                    "The first account must be an admin, so someone can "
                    "manage users afterwards."
                )
        else:
            acting = self.get_user(getattr(actor, 'username', ''))
            if (acting is None or acting.account_id != getattr(actor, 'account_id', '')
                    or not has_permission(acting, ACTION_MANAGE_USERS)):
                raise PermissionDeniedError(
                    "Only a user with the manage-users permission (an "
                    "admin) can create accounts."
                )

        salt = secrets.token_bytes(_SALT_BYTES)
        key = _derive_key(secret, salt, _PBKDF2_ITERATIONS)
        user = User(
            username=username,
            display_name=display_name,
            role=role,
            salt_hex=salt.hex(),
            key_hex=key.hex(),
            iterations=_PBKDF2_ITERATIONS,
            created_at=_now_iso(),
            account_id=secrets.token_hex(16),
        )
        self._users[username.lower()] = user
        self._save()
        if self.audit_log is not None:
            self.audit_log.record(
                EVENT_USER_CREATED,
                actor.username if actor is not None else "first-run setup",
                {
                    "username": user.username,
                    "display_name": user.display_name,
                    "role": user.role,
                },
            )
        return user

    # -- authentication -----------------------------------------------------

    @staticmethod
    def _confirmed_secret(secret: str, confirmation: str) -> None:
        if not secret:
            raise UserError("The PIN/password is blank.")
        if secret != confirmation:
            raise UserError("The PIN/password entries do not match. Enter both again.")

    def create_confirmed_user(self, *, confirmation: str, **account) -> User:
        """Interactive account creation: a typo cannot become the secret."""
        self._confirmed_secret(account.get("secret", ""), confirmation)
        return self.create_user(**account)

    def _replace_secret(self, user: User, secret: str) -> None:
        salt = secrets.token_bytes(_SALT_BYTES)
        previous = (user.salt_hex, user.key_hex, user.iterations)
        user.salt_hex = salt.hex()
        user.key_hex = _derive_key(secret, salt, _PBKDF2_ITERATIONS).hex()
        user.iterations = _PBKDF2_ITERATIONS
        try:
            self._save()
        except OSError:
            user.salt_hex, user.key_hex, user.iterations = previous
            raise

    @_fresh_accounts
    def reset_secret(self, *, actor: User, username: str, secret: str,
                     confirmation: str) -> User:
        """A signed-in admin resets any account, including another admin."""
        acting = self.get_user(getattr(actor, "username", ""))
        if (acting is None or acting.account_id != getattr(actor, 'account_id', '')
                or not has_permission(acting, ACTION_MANAGE_USERS)):
            raise PermissionDeniedError("Only an admin can reset another account's PIN/password.")
        self._confirmed_secret(secret, confirmation)
        target = self.get_user(username)
        if target is None:
            raise UserError("No such account.")
        self._replace_secret(target, secret)
        if self.audit_log is not None:
            self.audit_log.record("pin_reset", acting.username, {"target": target.username})
        return target

    @_fresh_accounts
    def change_secret(self, *, actor: User, current_secret: str,
                      secret: str, confirmation: str) -> User:
        """Self-service change requires the current secret, for every role."""
        self._confirmed_secret(secret, confirmation)
        target = self.authenticate(getattr(actor, "username", ""), current_secret)
        self._replace_secret(target, secret)
        if self.audit_log is not None:
            self.audit_log.record("pin_changed", target.username, {"target": target.username})
        return target

    @_fresh_accounts
    def authenticate(self, username: str, secret: str) -> User:
        """Return the User for a valid username/secret pair.

        Raises AuthenticationError otherwise (same message either way).
        """
        user = self.get_user(username)
        ok = False
        if user is not None and secret:
            candidate = _derive_key(
                secret, bytes.fromhex(user.salt_hex), user.iterations
            )
            ok = hmac.compare_digest(candidate, bytes.fromhex(user.key_hex))
        if self.audit_log is not None:
            self.audit_log.record(
                EVENT_LOGIN_SUCCEEDED if ok else EVENT_LOGIN_FAILED,
                user.username if user is not None else (username or ""),
                {"username": user.username if user is not None else username},
            )
        if not ok or user is None:
            raise AuthenticationError("Username or PIN/password is incorrect.")
        return user
