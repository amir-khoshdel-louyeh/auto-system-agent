"""Phase 1 item [17]: RBAC integration tests (FR-02, gate for Phase 1).

Gate: unauthorized users cannot access protected operations. Covers 401
(missing/malformed/expired/tampered token), 403 (role matrix over the
real routes), 201/200 for permitted roles, workspace-scoped role
overrides and the rule that knowing an identifier grants nothing.
"""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import create_app
from app.security import encode_token
from app.settings import Settings

SECRET = "rbac-integration-secret-0123456789"
ADMIN_PASSWORD = "rbac-admin-pw"


def make_client(tmp_path) -> TestClient:
    settings = Settings(
        admin_user="admin",
        admin_password=ADMIN_PASSWORD,
        secret_key=SECRET,
        sqlite_path=str(tmp_path / "auth.db"),
    )
    return TestClient(create_app(settings))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class RbacTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = make_client(Path(self._tmp.name))
        self.owner_token = self.client.post(
            "/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        ).json()["access_token"]
        # one user per role so memberships can reference real accounts
        self.tokens = {"OWNER": self.owner_token}
        for role in ("OPERATOR", "APPROVER", "VIEWER"):
            username = role.lower() + "_user"
            resp = self.client.post(
                "/admin/users",
                headers=bearer(self.owner_token),
                json={"username": username, "password": "pw-123456", "role": role},
            )
            assert resp.status_code == 201, resp.text
            self.tokens[role] = create_token(username, SECRET, 10, role=role)


class AuthenticationTests(RbacTestBase):
    PROTECTED = [
        ("GET", "/auth/me", None),
        ("GET", "/admin/users", None),
        ("POST", "/admin/users", {"username": "x_user", "password": "pw-123456"}),
        ("GET", "/workspaces", None),
        ("POST", "/workspaces", {"name": "anon-ws"}),
        ("POST", "/teams", {"name": "anon-team"}),
    ]

    def test_missing_token_is_401_with_challenge(self):
        for method, path, body in self.PROTECTED:
            with self.subTest(path=path):
                resp = self.client.request(method, path, json=body)
                self.assertEqual(resp.status_code, 401)
                self.assertIn("WWW-Authenticate", resp.headers)

    def test_wrong_scheme_and_garbage_tokens_are_401(self):
        for header in (
            {"Authorization": "Basic dXNlcjpwdw=="},
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer not.a.token"},
            {"Authorization": "Bearer " + "A" * 200},
        ):
            with self.subTest(header=header["Authorization"][:24]):
                self.assertEqual(self.client.get("/auth/me", headers=header).status_code, 401)

    def test_expired_token_is_401(self):
        expired = encode_token(
            {"sub": "admin", "role": "OWNER"},
            SECRET,
            ttl_seconds=60,
            now=time.time() - 3600,
        )
        self.assertEqual(self.client.get("/auth/me", headers=bearer(expired)).status_code, 401)

    def test_token_signed_with_other_secret_is_401(self):
        foreign = create_token("admin", "other-secret", 10, role="OWNER")
        self.assertEqual(self.client.get("/auth/me", headers=bearer(foreign)).status_code, 401)

    def test_tampered_token_is_401(self):
        tampered = self.owner_token[:-2] + ("AA" if not self.owner_token.endswith("AA") else "BB")
        self.assertEqual(self.client.get("/auth/me", headers=bearer(tampered)).status_code, 401)

    def test_me_returns_identity_and_permissions(self):
        resp = self.client.get("/auth/me", headers=bearer(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["username"], "admin")
        self.assertEqual(body["role"], "OWNER")
        self.assertIn("workspace:manage", body["permissions"])


class RoleMatrixTests(RbacTestBase):
    """403 for every role that lacks the permission, across real routes."""

    def test_admin_user_routes_require_owner(self):
        for role, token in self.tokens.items():
            expected = 201 if role == "OWNER" else 403
            with self.subTest(role=role, route="POST /admin/users"):
                resp = self.client.post(
                    "/admin/users",
                    headers=bearer(token),
                    json={"username": f"new-{role.lower()}", "password": "pw-123456"},
                )
                self.assertEqual(resp.status_code, expected)
            with self.subTest(role=role, route="GET /admin/users"):
                resp = self.client.get("/admin/users", headers=bearer(token))
                self.assertEqual(resp.status_code, 200 if role == "OWNER" else 403)

    def test_workspace_creation_requires_owner(self):
        for role, token in self.tokens.items():
            expected = 201 if role == "OWNER" else 403
            with self.subTest(role=role):
                resp = self.client.post(
                    "/workspaces",
                    headers=bearer(token),
                    json={"name": f"ws-{role.lower()}"},
                )
                self.assertEqual(resp.status_code, expected)

    def test_listing_workspaces_allowed_for_every_role(self):
        for role, token in self.tokens.items():
            with self.subTest(role=role):
                resp = self.client.get("/workspaces", headers=bearer(token))
                self.assertEqual(resp.status_code, 200)

    def test_membership_routes_require_owner(self):
        ws = self.client.post(
            "/workspaces",
            headers=bearer(self.owner_token),
            json={"name": "rbac-ws"},
        ).json()["id"]
        for role, token in self.tokens.items():
            expected = 201 if role == "OWNER" else 403
            with self.subTest(role=role, route="members"):
                resp = self.client.post(
                    f"/workspaces/{ws}/members",
                    headers=bearer(token),
                    json={"username": "operator_user", "role": "VIEWER"},
                )
                self.assertEqual(resp.status_code, expected)
            with self.subTest(role=role, route="member list"):
                resp = self.client.get(f"/workspaces/{ws}/members", headers=bearer(token))
                self.assertEqual(resp.status_code, 200 if role == "OWNER" else 403)

    def test_knowing_the_identifier_grants_nothing(self):
        # FR-02: a VIEWER knows the exact workspace and user identifiers and
        # still cannot mutate anything.
        ws = self.client.post(
            "/workspaces",
            headers=bearer(self.owner_token),
            json={"name": "known-ws"},
        ).json()["id"]
        viewer = self.tokens["VIEWER"]
        self.assertEqual(
            self.client.post(
                f"/workspaces/{ws}/members",
                headers=bearer(viewer),
                json={"username": "viewer_user", "role": "OWNER"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{ws}/members", headers=bearer(viewer)).status_code,
            403,
        )
        # privilege escalation through the role endpoint is impossible too
        resp = self.client.get(f"/workspaces/{ws}/role", headers=bearer(viewer))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["role"], "VIEWER")


class WorkspaceScopedRoleTests(RbacTestBase):
    def test_membership_overrides_global_role_inside_workspace(self):
        ws = self.client.post(
            "/workspaces",
            headers=bearer(self.owner_token),
            json={"name": "scoped-ws"},
        ).json()["id"]
        # operator_user is globally OPERATOR, membership downgrades to VIEWER
        self.client.post(
            f"/workspaces/{ws}/members",
            headers=bearer(self.owner_token),
            json={"username": "operator_user", "role": "VIEWER"},
        )
        resp = self.client.get(f"/workspaces/{ws}/role", headers=bearer(self.tokens["OPERATOR"]))
        self.assertEqual(resp.json()["role"], "VIEWER")

        # and an upgrade works the other way: globally VIEWER becomes OWNER here
        self.client.post(
            f"/workspaces/{ws}/members",
            headers=bearer(self.owner_token),
            json={"username": "viewer_user", "role": "OWNER"},
        )
        resp = self.client.get(f"/workspaces/{ws}/role", headers=bearer(self.tokens["VIEWER"]))
        self.assertEqual(resp.json()["role"], "OWNER")

    def test_unknown_workspace_membership_fails_closed(self):
        resp = self.client.get(
            "/workspaces/does-not-exist/role", headers=bearer(self.tokens["VIEWER"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["role"], "VIEWER")


class LoginFlowTests(RbacTestBase):
    def test_wrong_password_401_and_right_password_mints_owner(self):
        bad = self.client.post("/auth/login", json={"username": "admin", "password": "nope"})
        self.assertEqual(bad.status_code, 401)
        good = self.client.post(
            "/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        )
        self.assertEqual(good.status_code, 200)
        token = good.json()["access_token"]
        self.assertEqual(self.client.get("/auth/me", headers=bearer(token)).json()["role"], "OWNER")


if __name__ == "__main__":
    unittest.main()
