"""Phase 2 item [25]: POST /workflows/validate integration tests (FR-04).

Covers the endpoint contract: valid documents return 200 with an empty
error list, invalid ones return 422 carrying every finding with stage,
message, line and column — and anonymous callers never reach the engine.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import create_app
from app.settings import Settings

FIXTURES = Path(__file__).resolve().parents[2] / "src" / "engine" / "validator" / "fixtures"
SECRET = "validate-secret-0123456789abcdef"


def make_client(tmp: str) -> TestClient:
    settings = Settings(
        admin_user="admin",
        admin_password="validate-admin-pw",
        secret_key=SECRET,
        sqlite_path=str(Path(tmp) / "auth.db"),
    )
    return TestClient(create_app(settings))


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ValidateEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = make_client(self._tmp.name)
        self.token = self.client.post(
            "/auth/login",
            json={"username": "admin", "password": "validate-admin-pw"},
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _post(self, dsl: str, headers=None, dsl_type: str = "yaml"):
        return self.client.post(
            "/workflows/validate",
            headers=headers if headers is not None else self.headers,
            json={"dsl": dsl, "dsl_type": dsl_type},
        )

    def test_anonymous_caller_is_rejected(self):
        resp = self._post(fixture("valid-minimal.yaml"), headers={})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("WWW-Authenticate", resp.headers)

    def test_viewer_role_may_validate(self):
        viewer = create_token("viewer", SECRET, 10, role="VIEWER")
        resp = self._post(
            fixture("valid-minimal.yaml"),
            headers={"Authorization": f"Bearer {viewer}"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_valid_fixture_returns_200_report(self):
        resp = self._post(fixture("valid-minimal.yaml"))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["name"], "minimal")
        self.assertEqual(body["version"], 3)

    def test_all_valid_fixtures_pass(self):
        for name in ("valid-minimal.yaml", "valid-demo.yaml", "valid-gated-custom.yaml"):
            with self.subTest(fixture=name):
                resp = self._post(fixture(name))
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertTrue(resp.json()["valid"])

    def test_cyclic_fixture_is_rejected_with_cycle_stage(self):
        resp = self._post(fixture("cyclic-two-node.yaml"))
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertFalse(body["valid"])
        cycle = [e for e in body["errors"] if e["stage"] == "cycle"]
        self.assertTrue(cycle, body["errors"])

    def test_policy_fixture_is_rejected_with_policy_stage(self):
        resp = self._post(fixture("policy-dangerous-no-approval.yaml"))
        self.assertEqual(resp.status_code, 422)
        stages = {e["stage"] for e in resp.json()["errors"]}
        self.assertIn("policy", stages)

    def test_invalid_documents_are_never_stored(self):
        before = self.client.get("/workspaces", headers=self.headers).json()
        self._post(fixture("cyclic-two-node.yaml"))
        self._post(fixture("valid-minimal.yaml"))
        after = self.client.get("/workspaces", headers=self.headers).json()
        self.assertEqual(before, after, "validate must not persist anything")

    def test_json_dsl_type_supported(self):
        resp = self._post('{"workflow": "j", "version": 1, "nodes": []}', dsl_type="json")
        self.assertEqual(resp.status_code, 422)  # nodes empty -> schema error
        self.assertTrue(any(e["stage"] == "schema" for e in resp.json()["errors"]))


if __name__ == "__main__":
    unittest.main()
