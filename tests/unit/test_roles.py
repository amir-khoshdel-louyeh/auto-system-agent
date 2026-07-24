"""Phase 1 item [12]: OWNER/OPERATOR/APPROVER/VIEWER roles (FR-02, §5).

Asserts the exact frozen permission matrix, fail-closed parsing of unknown
roles, the env-admin role claim in minted JWTs and the least-privilege
default of the user_roles table.
"""

import tempfile
import unittest
from pathlib import Path

from app.auth import create_token
from app.db import get_role, init_db, set_role
from app.roles import (
    ALL_ROLES,
    PERMISSION_MATRIX,
    Permission,
    Role,
    has_permission,
    parse_role,
    permissions_for,
)
from app.security import TokenError, decode_token

EXPECTED = {
    Permission.WORKSPACE_MANAGE: {Role.OWNER},
    Permission.MEMBER_MANAGE: {Role.OWNER},
    Permission.WORKFLOW_EDIT: {Role.OWNER},
    Permission.WORKFLOW_RUN: {Role.OWNER, Role.OPERATOR},
    Permission.APPROVAL_DECIDE: {Role.OWNER, Role.APPROVER},
    Permission.RUN_READ: set(ALL_ROLES),
    Permission.AUDIT_READ: set(ALL_ROLES),
}


class PermissionMatrixTests(unittest.TestCase):
    def test_matrix_is_exactly_the_proposal_spec(self):
        for permission, expected_roles in EXPECTED.items():
            self.assertEqual(
                set(PERMISSION_MATRIX[permission]),
                expected_roles,
                f"wrong grantees for {permission}",
            )

    def test_viewer_cannot_mutate_or_decide(self):
        for permission in (
            Permission.WORKSPACE_MANAGE,
            Permission.MEMBER_MANAGE,
            Permission.WORKFLOW_EDIT,
            Permission.WORKFLOW_RUN,
            Permission.APPROVAL_DECIDE,
        ):
            self.assertFalse(has_permission(Role.VIEWER, permission))

    def test_operator_runs_but_does_not_approve(self):
        self.assertTrue(has_permission(Role.OPERATOR, Permission.WORKFLOW_RUN))
        self.assertFalse(has_permission(Role.OPERATOR, Permission.APPROVAL_DECIDE))

    def test_approver_decides_but_does_not_run(self):
        self.assertTrue(has_permission(Role.APPROVER, Permission.APPROVAL_DECIDE))
        self.assertFalse(has_permission(Role.APPROVER, Permission.WORKFLOW_RUN))

    def test_owner_holds_every_permission(self):
        self.assertEqual(permissions_for(Role.OWNER), frozenset(PERMISSION_MATRIX))


class RoleParsingTests(unittest.TestCase):
    def test_parse_accepts_role_instances_and_strings(self):
        self.assertIs(parse_role(Role.OWNER), Role.OWNER)
        self.assertIs(parse_role("operator"), Role.OPERATOR)
        self.assertIs(parse_role("  approver  "), Role.APPROVER)
        self.assertIs(parse_role("owner "), Role.OWNER)  # env vars get trimmed

    def test_parse_rejects_unknown_values(self):
        for bad in ("superuser", "", None, 7, ["OWNER"], "roles.owner"):
            with self.assertRaises(ValueError):
                parse_role(bad)

    def test_unknown_role_has_no_permissions(self):
        for bad in ("superuser", "", None):
            self.assertFalse(has_permission(bad, Permission.RUN_READ))  # type: ignore[arg-type]
            self.assertEqual(permissions_for(bad), frozenset())  # type: ignore[arg-type]


class RoleStorageTests(unittest.TestCase):
    def test_default_role_is_viewer_and_updates_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = init_db(Path(tmp) / "app.db")
            self.assertEqual(get_role(db, "missing-user"), Role.VIEWER)
            self.assertEqual(set_role(db, "u1", "operator", granted_by="seed"), Role.OPERATOR)
            self.assertEqual(get_role(db, "u1"), Role.OPERATOR)
            set_role(db, "u1", Role.OWNER)
            self.assertEqual(get_role(db, "u1"), Role.OWNER)
            with self.assertRaises(ValueError):
                set_role(db, "u1", "superuser")


class TokenRoleClaimTests(unittest.TestCase):
    def test_login_token_carries_configured_admin_role(self):
        from fastapi.testclient import TestClient

        from app.main import create_app
        from app.settings import Settings

        settings = Settings(admin_user="admin", admin_password="s3cret", secret_key="k" * 40)
        client = TestClient(create_app(settings))
        token = client.post("/auth/login", json={"username": "admin", "password": "s3cret"}).json()[
            "access_token"
        ]
        claims = decode_token(token, "k" * 40)
        self.assertEqual(claims["role"], "OWNER")

    def test_tokens_without_explicit_role_default_to_viewer(self):
        claims = decode_token(create_token("amir", "secret", ttl_minutes=5), "secret")
        self.assertEqual(claims["role"], "VIEWER")

    def test_invalid_admin_role_rejected_by_settings(self):
        from app.settings import Settings, SettingsError

        with self.assertRaises(SettingsError):
            Settings(admin_role="superuser")

    def test_modified_role_claim_fails_signature(self):
        import base64
        import json

        token = create_token("amir", "secret", ttl_minutes=5)
        head, body, signature = token.split(".")
        pad = "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(body + pad))
        self.assertEqual(claims["role"], "VIEWER")
        claims["role"] = "OWNER"  # privilege escalation attempt
        forged = (
            base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        with self.assertRaises(TokenError):
            decode_token(f"{head}.{forged}.{signature}", "secret")


if __name__ == "__main__":
    unittest.main()
