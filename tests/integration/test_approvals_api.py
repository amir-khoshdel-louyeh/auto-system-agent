"""Phase 8 item [77]: the approval and run-list endpoints the client reads.

These are the server side of the client screens: list requests, read one with
its decisions, cast a decision under ``approval:decide``, and list executions
for the run view. RBAC is checked on every route, and a forbidden transition
is a 409 with the state machine's reason.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.approvals import ApprovalService
from app.auth import create_token
from app.db import init_db
from app.main import create_app
from app.models import ExecutionStore, WorkflowStore
from app.settings import Settings

SECRET = "approvals-secret-0123456789abcd"

EXECUTION = "exec-0000000000000001"


class ApprovalsApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            admin_user="admin",
            admin_password="approvals-admin-pw",
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "app.db"),
        )
        self.client = TestClient(create_app(self.settings))
        token = self.client.post(
            "/auth/login", json={"username": "admin", "password": "approvals-admin-pw"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}
        self.db = init_db(self.settings.sqlite_path)
        self.approvals = ApprovalService(self.db)

    def _request(self, node_id: str = "deploy", *, quorum: int = 2):
        return self.approvals.request(EXECUTION, node_id, quorum=quorum)

    def test_authentication_is_required(self):
        self.assertEqual(self.client.get("/approvals").status_code, 401)
        self.assertEqual(self.client.get("/executions").status_code, 401)

    def test_approval_list_shows_pending_requests(self):
        self._request()
        body = self.client.get("/approvals", headers=self.headers).json()
        self.assertEqual(body["total"], 1)
        row = body["requests"][0]
        self.assertEqual(row["execution_id"], EXECUTION)
        self.assertEqual(row["node_id"], "deploy")
        self.assertEqual(row["quorum"], 2)
        self.assertEqual(row["status"], "PENDING")

    def test_approval_list_can_filter_by_execution(self):
        self._request("deploy")
        self.approvals.request("other-exec", "gate", quorum=1)
        body = self.client.get(
            "/approvals", params={"execution_id": EXECUTION}, headers=self.headers
        ).json()
        self.assertEqual([row["node_id"] for row in body["requests"]], ["deploy"])

    def test_read_one_request_with_its_decisions(self):
        self._request()
        self.approvals.decide(EXECUTION, "deploy", approver="alice", verdict="APPROVED")
        body = self.client.get(f"/approvals/{EXECUTION}/deploy", headers=self.headers).json()
        self.assertEqual(body["request"]["status"], "PENDING")
        self.assertEqual(len(body["decisions"]), 1)
        self.assertEqual(body["decisions"][0]["decided_by"], "alice")

    def test_unknown_request_is_404(self):
        response = self.client.get(f"/approvals/{EXECUTION}/missing", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_two_approvers_reach_the_quorum(self):
        self._request(quorum=2)
        first = self.client.post(
            f"/approvals/{EXECUTION}/deploy/decisions",
            json={"verdict": "APPROVED", "reason": "ok"},
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["request"]["status"], "PENDING")
        second = self.client.post(
            f"/approvals/{EXECUTION}/deploy/decisions",
            json={"verdict": "APPROVED"},
            headers={
                "Authorization": f"Bearer {create_token('approver2', SECRET, 10, role='APPROVER')}"
            },
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["request"]["status"], "APPROVED")

    def test_rejection_resolves_the_request(self):
        self._request()
        body = self.client.post(
            f"/approvals/{EXECUTION}/deploy/decisions",
            json={"verdict": "REJECTED", "reason": "no"},
            headers=self.headers,
        ).json()
        self.assertEqual(body["request"]["status"], "REJECTED")

    def test_viewer_cannot_decide(self):
        self._request()
        viewer = {"Authorization": f"Bearer {create_token('viewer', SECRET, 10, role='VIEWER')}"}
        response = self.client.post(
            f"/approvals/{EXECUTION}/deploy/decisions",
            json={"verdict": "APPROVED"},
            headers=viewer,
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_may_read_approvals(self):
        viewer = {"Authorization": f"Bearer {create_token('viewer', SECRET, 10, role='VIEWER')}"}
        self.assertEqual(self.client.get("/approvals", headers=viewer).status_code, 200)

    def test_invalid_verdict_is_rejected_by_the_schema(self):
        self._request()
        response = self.client.post(
            f"/approvals/{EXECUTION}/deploy/decisions",
            json={"verdict": "MAYBE"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_deciding_a_terminal_request_is_a_conflict(self):
        self._request()
        self.client.post(
            f"/approvals/{EXECUTION}/deploy/decisions",
            json={"verdict": "REJECTED"},
            headers=self.headers,
        )
        again = self.client.post(
            f"/approvals/{EXECUTION}/deploy/decisions",
            json={"verdict": "APPROVED"},
            headers=self.headers,
        )
        self.assertEqual(again.status_code, 409)
        self.assertIn("REJECTED", again.json()["detail"])


class ExecutionListTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        settings = Settings(
            admin_user="admin",
            admin_password="runs-admin-pw-123",
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "runs.db"),
        )
        self.client = TestClient(create_app(settings))
        token = self.client.post(
            "/auth/login", json={"username": "admin", "password": "runs-admin-pw-123"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}
        db = init_db(settings.sqlite_path)
        workflows = WorkflowStore(db)
        self.workflow_id = workflows.ensure_workflow("demo")
        self.executions = ExecutionStore(db)
        self.execution = self.executions.start(self.workflow_id, 1, run_id=EXECUTION)

    def test_run_list_returns_the_execution_summary(self):
        body = self.client.get("/executions", headers=self.headers).json()
        self.assertEqual(body["total"], 1)
        row = body["executions"][0]
        self.assertEqual(row["id"], EXECUTION)
        self.assertEqual(row["status"], "RUNNING")
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["workflow_id"], self.workflow_id)

    def test_limit_is_bounded(self):
        self.assertEqual(
            self.client.get("/executions", params={"limit": 0}, headers=self.headers).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
