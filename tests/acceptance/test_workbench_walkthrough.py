"""Phase 8 item [80]: the workbench walkthrough against a live API.

The unit walkthrough (`tests/unit/test_workbench_view.py`) proves the screens
format reports. This one proves the other half of the contract: the client's
HTTP layer talks to the real application over a socket. A uvicorn server is
started on an ephemeral port for the duration of each test, and every screen
is driven through :class:`WorkbenchClient` — login, validate, dry run, runs,
timeline, approvals, decisions, audit and chain verification.

The walkthrough also closes the Phase 8 gate from the client side: an
untrusted plan that would write outside its workspace is refused by the dry
run (`DENY`, `safe_to_run=False`) before any executor sees it.
"""

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import uvicorn

from app.approvals import ApprovalService
from app.db import init_db
from app.main import create_app
from app.models import ExecutionStore, WorkflowStore
from app.run_service import RunService
from app.settings import Settings
from storage.audit import AuditStore, AuditTrail
from ui.api_client import ApiError, WorkbenchClient
from ui.workbench import APPROVE, WorkbenchView

PASSWORD = "workbench-pw-123"
SECRET = "workbench-secret-0123456789ab"
RUN_ID = "run-0000000000000001"

DSL = """\
workflow: demo
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

ESCAPE_DSL = """\
workflow: untrusted
version: 1
triggers:
  - manual: true
nodes:
  - id: gate
    type: approval
    quorum: 1
  - id: wipe
    type: shell
    run: rm -rf ../outside
edges:
  - [gate, wipe]
policy: {}
"""

#: The same escape without the approval node the validator demands.
UNGATED_ESCAPE_DSL = ESCAPE_DSL.replace("  - id: gate\n    type: approval\n    quorum: 1\n", "")


def free_port() -> int:
    """Reserve an ephemeral port by binding and releasing it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveApi:
    """A real uvicorn server on a background thread, scoped to one test."""

    def __init__(self, settings: Settings) -> None:
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.app = create_app(settings)
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self, timeout: float = 10.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("API did not start within the deadline")
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


class WalkthroughBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            admin_user="admin",
            admin_password=PASSWORD,
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "workbench.db"),
        )
        self.api = LiveApi(self.settings)
        self.api.start()
        self.addCleanup(self.api.stop)
        self.client = WorkbenchClient(self.api.base_url, timeout=10)
        self.token = self.client.login("admin", PASSWORD)
        self.view = WorkbenchView(self.client)
        self.db = init_db(self.settings.sqlite_path)

    def seed_run(self) -> str:
        """One running execution with two recorded step attempts."""
        workflow_id = WorkflowStore(self.db).ensure_workflow("demo")
        executions = ExecutionStore(self.db)
        executions.start(workflow_id, 1, triggered_by="manual", run_id=RUN_ID)
        executions.record_step(RUN_ID, "read", "SUCCEEDED")
        executions.record_step(RUN_ID, "gate", "FAILED", attempt=2, error="boom")
        return workflow_id


class LoginTests(WalkthroughBase):
    def test_login_keeps_the_bearer_token(self):
        self.assertTrue(self.token)
        self.assertEqual(self.client.token, self.token)

    def test_anonymous_client_is_refused(self):
        anonymous = WorkbenchClient(self.api.base_url, timeout=10)
        with self.assertRaises(ApiError) as caught:
            anonymous.executions()
        self.assertEqual(caught.exception.status, 401)


class WorkflowScreenTests(WalkthroughBase):
    def test_validate_over_http_reports_the_workflow(self):
        self.assertEqual(self.view.validate(DSL), ["OK demo v1"])

    def test_invalid_dsl_renders_findings_instead_of_raising(self):
        lines = self.view.validate("workflow: demo\nnodes:\n  - id: a\n    type: teleport\n")
        self.assertEqual(self.view.status, "invalid")
        self.assertTrue(lines)
        self.assertIn("teleport", " ".join(lines))

    def test_dry_run_reports_every_node_and_stays_inert(self):
        lines = self.view.dry_run(DSL)
        self.assertEqual(self.view.status, "ready")
        self.assertTrue(any(line.startswith("read [ALLOW]") for line in lines), lines)
        self.assertIn("persisted=False", lines[-1])
        self.assertIn("dispatched=False", lines[-1])
        self.assertIn("safe_to_run=True", lines[-1])

    def test_untrusted_plan_is_blocked_by_the_dry_run(self):
        lines = self.view.dry_run(ESCAPE_DSL)
        self.assertEqual(self.view.status, "blocked")
        self.assertTrue(any(line.startswith("wipe [DENY]") for line in lines), lines)
        self.assertIn("safe_to_run=False", lines[-1])

    def test_untrusted_plan_without_an_approval_node_is_invalid(self):
        lines = self.view.dry_run(UNGATED_ESCAPE_DSL)
        self.assertEqual(self.view.status, "invalid")
        self.assertTrue(any("no approval node" in line for line in lines), lines)


class RunScreenTests(WalkthroughBase):
    def test_runs_and_timeline_come_from_the_durable_records(self):
        self.seed_run()
        rows = self.view.runs()
        self.assertEqual(self.view.run_ids, [RUN_ID])
        self.assertIn("RUNNING", rows[0])
        self.assertIn(RUN_ID[:8], rows[0])

        timeline = self.view.timeline(RUN_ID)
        self.assertIn("RUNNING demo v1", timeline[0])
        self.assertTrue(any("read [SUCCEEDED] attempt 1" in line for line in timeline))
        self.assertTrue(any("gate [FAILED] attempt 2 — boom" in line for line in timeline))

    def test_unknown_run_is_an_api_error(self):
        with self.assertRaises(ApiError) as caught:
            self.view.timeline("missing-run")
        self.assertEqual(caught.exception.status, 404)


class ApprovalScreenTests(WalkthroughBase):
    def setUp(self):
        super().setUp()
        self.seed_run()
        self.approvals = ApprovalService(self.db)
        self.approvals.request(RUN_ID, "deploy", quorum=1)

    def test_approvals_screen_lists_the_pending_request(self):
        lines = self.view.approvals()
        self.assertEqual(self.view.approval_ids, [(RUN_ID, "deploy")])
        self.assertIn("PENDING", lines[0])
        self.assertIn("quorum 1", lines[0])

    def test_approve_over_http_resolves_the_request(self):
        self.view.approvals()
        message = self.view.decide(RUN_ID, "deploy", APPROVE, reason="checked")
        self.assertIn("APPROVED", message)
        self.assertEqual(self.view.status, "APPROVED")
        self.assertIn("APPROVED", self.view.approvals()[0])
        self.assertEqual(self.approvals.status(RUN_ID, "deploy"), "APPROVED")

    def test_deciding_twice_is_a_conflict_not_a_silent_no_op(self):
        self.view.decide(RUN_ID, "deploy", APPROVE)
        with self.assertRaises(ApiError) as caught:
            self.view.decide(RUN_ID, "deploy", APPROVE)
        self.assertEqual(caught.exception.status, 409)


class AuditScreenTests(WalkthroughBase):
    def setUp(self):
        super().setUp()
        self.seed_run()
        self.store = AuditStore(self.db)
        trail = AuditTrail(self.store, RUN_ID)
        trail.run_started("demo", 1)
        trail.dispatch("read")
        trail.result("read", "SUCCEEDED", output_summary="ok")

    def test_audit_page_and_verification_agree(self):
        lines = self.view.audit(RUN_ID)
        self.assertIn("3 event(s)", lines[0])
        self.assertIn("#0 run.started", lines[1])
        self.assertIn("#2 node.result", lines[3])
        self.assertEqual(self.view.verify(RUN_ID), "chain OK — 3 event(s) verified")

    def test_tampering_is_reported_by_the_verify_screen(self):
        import sqlite3

        with sqlite3.connect(self.settings.sqlite_path) as conn:
            conn.execute(
                "UPDATE audit_events SET payload = ? WHERE execution_id = ? AND seq = 1",
                ('{"run_id":"forged"}', RUN_ID),
            )
        self.assertIn("chain BROKEN at seq 1", self.view.verify(RUN_ID))


class SaveAndRunOverHttpTests(WalkthroughBase):
    """Item [104]/[106]: the Workflow tab publishes and starts a real run."""

    async def fake_shell(self, node_id, node, ctx):
        """Keep the acceptance path hermetic: no OS sandbox needed in CI."""
        return {"ran": node.get("params", {}).get("run", ""), "exit_code": 0}

    def test_save_run_and_inspect_a_run_from_the_client(self):
        self.api.app.state.run_service = RunService(
            self.settings.sqlite_path, exec_fn=self.fake_shell
        )
        self.assertEqual(self.view.validate(DSL), ["OK demo v1"])
        self.assertIn("Saved demo v1", self.view.save(DSL)[0])
        self.assertTrue(self.view.workflow_id)

        self.assertIn("RUNNING", self.view.run()[0])
        execution_id = self.view.execution_id
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            timeline = self.view.timeline(execution_id)
            if not timeline[0].startswith("RUNNING"):
                break
            time.sleep(0.02)
        self.assertIn("COMPLETED", timeline[0])
        self.assertTrue(any("read [SUCCEEDED]" in line for line in timeline), timeline)

        rows = self.view.runs()
        self.assertIn(execution_id[:8], "\n".join(rows))
        events = self.view.audit(execution_id)
        self.assertIn("run.started", "\n".join(events))
        self.assertIn("chain OK", self.view.verify(execution_id))

    def test_replay_from_a_completed_run_matches(self):
        self.api.app.state.run_service = RunService(
            self.settings.sqlite_path, exec_fn=self.fake_shell
        )
        self.view.save(DSL)
        self.view.run()
        execution_id = self.view.execution_id
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.view.timeline(execution_id)[0].startswith("RUNNING"):
                break
            time.sleep(0.02)

        result = self.client.replay(execution_id)
        self.assertTrue(result["comparison"]["match"])
        self.assertNotEqual(result["replay_execution_id"], execution_id)


class FullWalkthroughTests(WalkthroughBase):
    def test_every_screen_works_against_the_live_api(self):
        self.seed_run()
        self.approvals = ApprovalService(self.db)
        self.approvals.request(RUN_ID, "deploy", quorum=1)

        self.assertEqual(self.view.validate(DSL), ["OK demo v1"])
        self.assertTrue(self.view.dry_run(DSL)[-1].startswith("summary:"))
        self.view.runs()
        self.assertTrue(self.view.timeline(self.view.run_ids[0])[0].startswith("RUNNING"))
        self.view.approvals()
        self.assertIn("APPROVED", self.view.decide(*self.view.approval_ids[0], APPROVE))
        self.assertTrue(self.view.audit(self.view.run_ids[0]))
        self.assertIn("chain OK", self.view.verify(self.view.run_ids[0]))


if __name__ == "__main__":
    unittest.main()
