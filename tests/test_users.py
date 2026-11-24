"""Tests for local users + RBAC (Stage 3).

Headless: a UserStore over a temp file. Verifies the permission matrix,
the first-run rule, salted-hash storage (the secret never appears in
the file), and authentication success/failure.

Run from the repo root:
    python3 -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealerpaperwork.audit import (
    EVENT_LOGIN_FAILED,
    EVENT_LOGIN_SUCCEEDED,
    EVENT_USER_CREATED,
    AuditLog,
)
from dealerpaperwork.users import (
    ACTION_APPROVE_PRESET,
    ACTION_CERTIFY_DEAL,
    ACTION_EDIT_DEAL,
    ACTION_MANAGE_USERS,
    ACTIONS,
    PERMISSIONS,
    ROLES,
    AuthenticationError,
    DuplicateUserError,
    PermissionDeniedError,
    UserError,
    UserStore,
    has_permission,
)

ADMIN_SECRET = "admin-pin-0000"
FI_SECRET = "fi-pin-1111"


class UserStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "users.json"
        self.store = UserStore(self.path)

    def make_admin(self):
        return self.store.create_user(
            actor=None,
            username="ada",
            display_name="Ada Admin",
            role="admin",
            secret=ADMIN_SECRET,
        )

    def make_user(self, admin, username, role, secret):
        return self.store.create_user(
            actor=admin,
            username=username,
            display_name=username.title(),
            role=role,
            secret=secret,
        )


class TestFirstRun(UserStoreTestCase):
    def test_first_account_must_be_admin_and_needs_no_actor(self):
        admin = self.make_admin()
        self.assertEqual(admin.role, "admin")
        self.assertFalse(self.store.is_empty)

    def test_first_account_cannot_be_non_admin(self):
        with self.assertRaises(PermissionDeniedError):
            self.store.create_user(
                actor=None,
                username="sal",
                display_name="Sal",
                role="sales",
                secret="whatever",
            )

    def test_after_first_run_creating_users_requires_an_admin(self):
        admin = self.make_admin()
        fi = self.make_user(admin, "fay", "fi", FI_SECRET)
        with self.assertRaises(PermissionDeniedError):
            self.store.create_user(
                actor=fi,
                username="mallory",
                display_name="Mallory",
                role="sales",
                secret="x",
            )
        with self.assertRaises(PermissionDeniedError):
            self.store.create_user(
                actor=None,
                username="mallory",
                display_name="Mallory",
                role="sales",
                secret="x",
            )

    def test_duplicate_username_rejected(self):
        admin = self.make_admin()
        with self.assertRaises(DuplicateUserError):
            self.make_user(admin, "ADA", "manager", "other-secret")

    def test_unknown_role_rejected(self):
        with self.assertRaises(UserError):
            self.store.create_user(
                actor=None,
                username="x",
                display_name="X",
                role="superuser",
                secret="x",
            )


class TestPermissionMatrix(UserStoreTestCase):
    EXPECTED = {
        "admin": {
            ACTION_MANAGE_USERS,
            ACTION_APPROVE_PRESET,
            ACTION_CERTIFY_DEAL,
            ACTION_EDIT_DEAL,
        },
        "manager": {
            ACTION_APPROVE_PRESET,
            ACTION_CERTIFY_DEAL,
            ACTION_EDIT_DEAL,
        },
        "fi": {ACTION_CERTIFY_DEAL, ACTION_EDIT_DEAL},
        "sales": {ACTION_EDIT_DEAL},
    }

    def test_matrix_matches_the_spec_table(self):
        admin = self.make_admin()
        users = {"admin": admin}
        for role in ("manager", "fi", "sales"):
            users[role] = self.make_user(
                admin, f"{role}_user", role, f"{role}-secret"
            )
        for role in ROLES:
            self.assertEqual(set(PERMISSIONS[role]), self.EXPECTED[role])
            for action in ACTIONS:
                self.assertEqual(
                    has_permission(users[role], action),
                    action in self.EXPECTED[role],
                    f"{role} / {action}",
                )

    def test_sales_cannot_certify_or_approve_or_manage(self):
        admin = self.make_admin()
        sales = self.make_user(admin, "sal", "sales", "sal-secret")
        self.assertFalse(has_permission(sales, ACTION_CERTIFY_DEAL))
        self.assertFalse(has_permission(sales, ACTION_APPROVE_PRESET))
        self.assertFalse(has_permission(sales, ACTION_MANAGE_USERS))
        self.assertTrue(has_permission(sales, ACTION_EDIT_DEAL))

    def test_only_admin_manages_users(self):
        admin = self.make_admin()
        for role in ("manager", "fi", "sales"):
            user = self.make_user(admin, f"u_{role}", role, "secret-x")
            self.assertFalse(has_permission(user, ACTION_MANAGE_USERS))


class TestAuthentication(UserStoreTestCase):
    def test_correct_secret_authenticates(self):
        self.make_admin()
        user = self.store.authenticate("ada", ADMIN_SECRET)
        self.assertEqual(user.username, "ada")
        self.assertEqual(user.role, "admin")

    def test_wrong_secret_fails(self):
        self.make_admin()
        with self.assertRaises(AuthenticationError):
            self.store.authenticate("ada", "not-the-pin")

    def test_unknown_user_fails_the_same_way(self):
        self.make_admin()
        with self.assertRaises(AuthenticationError):
            self.store.authenticate("ghost", ADMIN_SECRET)

    def test_secret_is_never_stored_in_plaintext(self):
        admin = self.make_admin()
        self.make_user(admin, "fay", "fi", FI_SECRET)
        stored = self.path.read_text(encoding="utf-8")
        self.assertNotIn(ADMIN_SECRET, stored)
        self.assertNotIn(FI_SECRET, stored)
        # The verifier fields exist and are per-user salted: the same
        # store reloaded from disk still authenticates.
        reloaded = UserStore(self.path)
        self.assertEqual(
            reloaded.authenticate("fay", FI_SECRET).username, "fay"
        )
        self.assertNotEqual(
            reloaded.get_user("ada").salt_hex,
            reloaded.get_user("fay").salt_hex,
        )


class TestUserAuditWiring(unittest.TestCase):
    def test_user_created_and_login_events_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(Path(tmp) / "audit.jsonl")
            store = UserStore(Path(tmp) / "users.json", audit_log=audit)
            store.create_user(
                actor=None,
                username="ada",
                display_name="Ada Admin",
                role="admin",
                secret=ADMIN_SECRET,
            )
            store.authenticate("ada", ADMIN_SECRET)
            with self.assertRaises(AuthenticationError):
                store.authenticate("ada", "wrong")
            types = [e.event_type for e in audit.read_events()]
            self.assertEqual(
                types,
                [EVENT_USER_CREATED, EVENT_LOGIN_SUCCEEDED, EVENT_LOGIN_FAILED],
            )


if __name__ == "__main__":
    unittest.main()
