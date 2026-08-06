"""Phase 11 item [104]: create a workflow, start a run, inspect it, replay it.

The API is the only path the workbench has, so these tests drive exactly the
four new routes over HTTP:

- `POST /workflows` publishes a validated immutable version (and rejects an
  invalid document with the frozen finding shape, persisting nothing);
- `POST /executions` answers 202 immediately and then the DAG really runs:
  the shell node executes, the approval node is decided, the steps are
  persisted and the execution's audit chain verifies;
- `GET /executions/{id}` returns the durable run (404 when unknown);
- `POST /executions/{id}/replay` re-runs the pinned version and reports a
  match against the original outputs.

A GUI node is also started on purpose: the stub worker must fail the step
closed with its reason, not silently drop it.
"""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import create_token
from app.db import init_db
from app.main import create_app
from app.models import ExecutionStore, WorkflowStore
from app.run_service import RunService
from app.settings import Settings
from storage.audit import AuditStore

SECRET = "execution-api-secret-0123456789"
PASSWORD = "execution-admin-pw"

DSL = """\
workflow: api-demo
version: 1
triggers:
  - manual: true
nodes:
  - id: make
    type: shell
    run: echo hello
  - id: gate
    type: approval
    quorum: 1
edges:
  - [make, gate]
policy: {}
"""

GUI_DSL = """\
workflow: api-gui
version: 1
triggers:
  - manual: true
nodes:
  - id: gate
    type: approval
    quorum: 1
  - id: click
    type: gui_click
    target: "Settings > Appearance"
edges:
  - [gate, click]
policy: {}
"""


class ExecutionApiBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            admin_user="admin",
            admin_password=PASSWORD,
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "executions.db"),
        )
        self.app = create_app(self.settings)
        self.app.state.run_service = RunService(self.settings.sqlite_path, exec_fn=self.fake_shell)
        self.client = TestClient(self.app)
        self.headers = {"Authorization": f"Bearer {self.login()}"}
        self.db = init_db(self.settings.sqlite_path)

    # -- helpers ---------------------------------------------------------
    def login(self) -> str:
        response = self.client.post("/auth/login", json={"username": "admin", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    def bearer(self, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_token('user', SECRET, 10, role=role)}"}

    async def fake_shell(self, node_id, node, ctx):
        """Deterministic stand-in for the sandboxed command runner."""
        return {"ran": node.get("params", {}).get("run", ""), "exit_code": 0, "stdout": "hello"}

    def create(self, dsl: str = DSL) -> dict:
        response = self.client.post(
            "/workflows", json={"dsl": dsl, "dsl_type": "yaml"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def start(self, workflow_id: str, version: int = 1) -> dict:
        response = self.client.post(
            "/executions",
            json={"workflow_id": workflow_id, "version": version},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def wait(self, execution_id: str, timeout: float = 10.0) -> dict:
        """Poll the durable row until the background thread finishes it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/executions/{execution_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            execution = response.json()
            if execution["status"] != "RUNNING":
                self.app.state.run_service.join(execution_id, timeout=5)
                return execution
            time.sleep(0.02)
        self.fail(f"execution {execution_id} did not finish within {timeout:g}s")


class CreateWorkflowTests(ExecutionApiBase):
    def test_valid_document_is_published_as_one_immutable_version(self):
        report = self.create()
        self.assertTrue(report["valid"])
        self.assertEqual(report["name"], "api-demo")
        self.assertEqual(report["version"], 1)
        row = WorkflowStore(self.db).get_version(report["workflow_id"], 1)
        self.assertIsNotNone(row)
        self.assertIn("api-demo", row["dsl_text"])

    def test_invalid_document_is_a_422_with_frozen_findings_and_no_row(self):
        response = self.client.post(
            "/workflows",
            json={"dsl": "workflow: bad\nnodes:\n  - id: a\n    type: teleport\n"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertTrue(body["errors"])
        for finding in body["errors"]:
            self.assertEqual(set(finding), {"stage", "message", "line", "column"})
            self.assertGreaterEqual(finding["line"], 1)
            self.assertGreaterEqual(finding["column"], 1)
        self.assertEqual(WorkflowStore(self.db).list_workflows(), [])

    def test_unparsable_yaml_is_a_422_with_a_position(self):
        response = self.client.post(
            "/workflows", json={"dsl": "workflow: [unclosed"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["errors"][0]["stage"], "yaml")

    def test_republishing_changed_text_under_the_same_version_is_409(self):
        self.create()
        edited = DSL.replace("echo hello", "echo goodbye")
        response = self.client.post("/workflows", json={"dsl": edited}, headers=self.headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("immutable", response.json()["detail"])

    def test_editing_needs_workflow_edit(self):
        anonymous = self.client.post("/workflows", json={"dsl": DSL})
        self.assertEqual(anonymous.status_code, 401)
        operator = self.client.post(
            "/workflows", json={"dsl": DSL}, headers=self.bearer("OPERATOR")
        )
        self.assertEqual(operator.status_code, 403)


class WorkflowLibraryTests(ExecutionApiBase):
    """Item [108]: reopen a saved workflow after a restart."""

    def test_library_lists_the_published_versions(self):
        created = self.create()
        response = self.client.get("/workflows", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 1)
        workflow = body["workflows"][0]
        self.assertEqual(workflow["id"], created["workflow_id"])
        self.assertEqual(workflow["name"], "api-demo")
        self.assertEqual([v["version"] for v in workflow["versions"]], [1])

    def test_a_version_returns_its_exact_dsl(self):
        workflow_id = self.create()["workflow_id"]
        response = self.client.get(f"/workflows/{workflow_id}/versions/1", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["dsl_text"], DSL)
        self.assertEqual(body["name"], "api-demo")
        self.assertEqual(len(body["dsl_hash"]), 64)

    def test_reading_the_library_only_needs_run_read(self):
        workflow_id = self.create()["workflow_id"]
        viewer = self.bearer("VIEWER")
        self.assertEqual(self.client.get("/workflows", headers=viewer).status_code, 200)
        self.assertEqual(
            self.client.get(f"/workflows/{workflow_id}/versions/1", headers=viewer).status_code,
            200,
        )
        self.assertEqual(self.client.get("/workflows").status_code, 401)

    def test_unknown_version_is_a_404(self):
        workflow_id = self.create()["workflow_id"]
        response = self.client.get(f"/workflows/{workflow_id}/versions/9", headers=self.headers)
        self.assertEqual(response.status_code, 404, response.text)


class StartRunTests(ExecutionApiBase):
    def test_run_completes_persists_steps_and_audits_itself(self):
        workflow_id = self.create()["workflow_id"]
        started = self.start(workflow_id)
        self.assertEqual(started["status"], "RUNNING")
        self.assertEqual(started["workflow_id"], workflow_id)
        self.assertEqual(started["version"], 1)

        execution = self.wait(started["execution_id"])
        self.assertEqual(execution["status"], "COMPLETED")
        steps = {step["node_id"]: step for step in execution["steps"]}
        self.assertEqual(set(steps), {"make", "gate"})
        self.assertEqual({step["state"] for step in steps.values()}, {"SUCCEEDED"})
        self.assertIn("hello", steps["make"]["output_summary"])

        events = [row["event"] for row in AuditStore(self.db).events(started["execution_id"])]
        self.assertEqual(events[0], "run.started")
        self.assertEqual(events[-1], "run.finished")
        self.assertIn("policy.decision", events)
        self.assertIn("node.dispatched", events)
        self.assertIn("node.result", events)
        verify = self.client.get(
            f"/executions/{started['execution_id']}/audit/verify", headers=self.headers
        )
        self.assertEqual(verify.status_code, 200)
        self.assertTrue(verify.json()["ok"])

        # The request thread persisted the row; the run must not record a second.
        self.assertEqual(len(ExecutionStore(self.db).list_executions()), 1)

    def test_gui_node_fails_closed(self):
        """A GUI node never runs: the policy gate blocks it before dispatch.

        The run path also wires the `gui_click`/`gui_type` stub workers, so a
        GUI task can only ever answer `step.failed` with the stub reason. The
        gate fires first today (NEED_APPROVAL is never fulfilled by the
        scheduler), and the durable state records the block.
        """
        workflow_id = self.create(GUI_DSL)["workflow_id"]
        execution = self.wait(self.start(workflow_id)["execution_id"])
        self.assertEqual(execution["status"], "FAILED")
        steps = {step["node_id"]: step for step in execution["steps"]}
        self.assertEqual(steps["gate"]["state"], "SUCCEEDED")
        self.assertEqual(steps["click"]["state"], "BLOCKED")
        self.assertIn("needs-approval", steps["click"]["error"])
        self.assertTrue(AuditStore(self.db).verify(execution["id"]))

    def test_a_run_that_cannot_execute_is_persisted_as_failed(self):
        # Bypass the API to pin a document that fails validation inside the
        # run thread, so the error path (not the normal node result) is used.
        store = WorkflowStore(self.db)
        workflow_id = store.ensure_workflow("broken")
        store.publish_version(workflow_id, 1, "workflow: [unclosed")

        execution = self.wait(self.start(workflow_id)["execution_id"])
        self.assertEqual(execution["status"], "FAILED")
        self.assertIn("YAML syntax error", execution["error"])

    def test_unknown_version_is_a_404(self):
        workflow_id = self.create()["workflow_id"]
        response = self.client.post(
            "/executions",
            json={"workflow_id": workflow_id, "version": 9},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_running_needs_workflow_run(self):
        workflow_id = self.create()["workflow_id"]
        anonymous = self.client.post("/executions", json={"workflow_id": workflow_id, "version": 1})
        self.assertEqual(anonymous.status_code, 401)
        viewer = self.client.post(
            "/executions",
            json={"workflow_id": workflow_id, "version": 1},
            headers=self.bearer("VIEWER"),
        )
        self.assertEqual(viewer.status_code, 403)
        operator = self.client.post(
            "/executions",
            json={"workflow_id": workflow_id, "version": 1},
            headers=self.bearer("OPERATOR"),
        )
        self.assertEqual(operator.status_code, 202, operator.text)
        self.wait(operator.json()["execution_id"])

    def test_unknown_execution_is_a_404(self):
        response = self.client.get("/executions/missing-run", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        viewer = self.client.get("/executions/missing-run", headers=self.bearer("VIEWER"))
        self.assertEqual(viewer.status_code, 404)


class ReplayTests(ExecutionApiBase):
    def test_replay_reruns_the_pinned_version_and_matches(self):
        workflow_id = self.create()["workflow_id"]
        source = self.wait(self.start(workflow_id)["execution_id"])

        response = self.client.post(f"/executions/{source['id']}/replay", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["source_execution_id"], source["id"])
        self.assertNotEqual(result["replay_execution_id"], source["id"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertTrue(result["comparison"]["match"])
        self.assertEqual(result["comparison"]["compared"], 2)

        replay = self.client.get(
            f"/executions/{result['replay_execution_id']}", headers=self.headers
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["triggered_by"], "replay")
        self.assertEqual({step["state"] for step in replay.json()["steps"]}, {"SUCCEEDED"})

        events = [row["event"] for row in AuditStore(self.db).events(source["id"])]
        self.assertEqual(events[-1], "run.replay")

    def test_unknown_execution_cannot_be_replayed(self):
        response = self.client.post("/executions/missing-run/replay", headers=self.headers)
        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
