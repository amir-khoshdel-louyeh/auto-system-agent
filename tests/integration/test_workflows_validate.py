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
from app.workflows_api import STAGES

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


class PreciseErrorResponseTests(unittest.TestCase):
    """Item [26]: every finding is `{stage, message, line, column}` and precise.

    `stage` is drawn from a frozen vocabulary, positions are always 1-based
    (parser errors included) and *all* findings are returned together so a
    client never has to re-submit to discover the next problem.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = make_client(self._tmp.name)
        token = self.client.post(
            "/auth/login",
            json={"username": "admin", "password": "validate-admin-pw"},
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def _post(self, dsl, dsl_type="yaml"):
        return self.client.post(
            "/workflows/validate",
            headers=self.headers,
            json={"dsl": dsl, "dsl_type": dsl_type},
        )

    def _errors(self, resp):
        return resp.json()["errors"]

    def test_findings_carry_exactly_the_frozen_shape(self):
        resp = self._post(fixture("invalid-bad-type.yaml"))
        self.assertEqual(resp.status_code, 422)
        self.assertTrue(self._errors(resp))
        for err in self._errors(resp):
            self.assertEqual(set(err), {"stage", "message", "line", "column"})
            self.assertIsInstance(err["message"], str)
            self.assertTrue(err["message"])

    def test_every_stage_is_in_the_frozen_vocabulary(self):
        for name in (
            "invalid-bad-type.yaml",
            "invalid-unknown-edge.yaml",
            "cyclic-two-node.yaml",
            "policy-dangerous-no-approval.yaml",
        ):
            with self.subTest(fixture=name):
                for err in self._errors(self._post(fixture(name))):
                    self.assertIn(err["stage"], STAGES, err)

    def test_positions_are_always_one_based(self):
        for name in ("invalid-bad-type.yaml", "cyclic-two-node.yaml"):
            with self.subTest(fixture=name):
                for err in self._errors(self._post(fixture(name))):
                    self.assertGreaterEqual(err["line"], 1, err)
                    self.assertGreaterEqual(err["column"], 1, err)

    def test_yaml_parse_error_reports_yaml_stage_with_position(self):
        broken = "workflow: w\nversion: 1\nnodes:\n  - id: a\n   type: shell\n"
        resp = self._post(broken)
        self.assertEqual(resp.status_code, 422)
        (err,) = self._errors(resp)
        self.assertEqual(err["stage"], "yaml")
        self.assertGreaterEqual(err["line"], 4)
        self.assertGreaterEqual(err["column"], 1)
        self.assertIn("YAML syntax error", err["message"])

    def test_json_parse_error_reports_json_stage_with_position(self):
        resp = self._post('{"workflow": "w",,}', dsl_type="json")
        self.assertEqual(resp.status_code, 422)
        (err,) = self._errors(resp)
        self.assertEqual(err["stage"], "json")
        self.assertGreaterEqual(err["line"], 1)
        self.assertGreaterEqual(err["column"], 1)

    def test_all_findings_are_returned_together(self):
        doc = (
            "workflow: w\nversion: 1\nnodes:\n"
            "  - id: wipe\n    type: shell\n    run: rm -rf /tmp/data\n"
            "edges: [[wipe, ghost]]\n"
        )
        stages = {e["stage"] for e in self._errors(self._post(doc))}
        self.assertIn("edge", stages)
        self.assertIn("policy", stages)


if __name__ == "__main__":
    unittest.main()
