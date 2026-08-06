"""Phase 5 items [49],[50]: audit query and execution timeline endpoints.

Read-only, RBAC-protected endpoints over the append-only audit chain and the
durable execution timeline; evidence is seeded directly in the same SQLite
database the API opens.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import create_token
from app.db import init_db
from app.main import create_app
from app.models import ExecutionStore, WorkflowStore
from app.settings import Settings
from storage.audit import AuditStore

SECRET = "audit-secret-0123456789abcdef"


class AuditApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            admin_user="admin",
            admin_password="audit-admin-pw",
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "auth.db"),
        )
        self.client = TestClient(create_app(self.settings))
        self.token = self.client.post(
            "/auth/login", json={"username": "admin", "password": "audit-admin-pw"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

        db = init_db(self.settings.sqlite_path)
        wid = WorkflowStore(db).ensure_workflow("demo")
        store = ExecutionStore(db)
        store.start(wid, 3, triggered_by="file.arrived", run_id="run-1")
        store.record_step("run-1", "a", "SUCCEEDED", attempt=1, output_summary="{}")
        store.record_step("run-1", "b", "FAILED", attempt=2, error="boom")
        store.finish("run-1", "FAILED", "1 node not OK")

        audit = AuditStore(db)
        for n in range(5):
            audit.append("run-1", "node.finished", {"n": n})

    def test_anonymous_caller_is_rejected(self):
        resp = self.client.get("/executions/run-1/audit")
        self.assertEqual(resp.status_code, 401)

    def test_viewer_role_may_read_audit(self):
        viewer = create_token("viewer", SECRET, 10, role="VIEWER")
        resp = self.client.get(
            "/executions/run-1/audit", headers={"Authorization": f"Bearer {viewer}"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_audit_query_reports_total_and_page(self):
        resp = self.client.get(
            "/executions/run-1/audit", headers=self.headers, params={"limit": 2, "offset": 1}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["limit"], 2)
        self.assertEqual([e["seq"] for e in body["events"]], [1, 2])

    def test_audit_query_validates_pagination(self):
        resp = self.client.get("/executions/run-1/audit", headers=self.headers, params={"limit": 0})
        self.assertEqual(resp.status_code, 422)

    def test_timeline_returns_execution_and_steps(self):
        resp = self.client.get("/executions/run-1/timeline", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "FAILED")
        self.assertEqual(body["version"], 3)
        self.assertEqual(body["triggered_by"], "file.arrived")
        steps = body["steps"]
        self.assertEqual([s["node_id"] for s in steps], ["a", "b"])
        self.assertEqual(steps[1]["attempt"], 2)
        self.assertEqual(steps[1]["error"], "boom")

    def test_timeline_unknown_execution_is_404(self):
        resp = self.client.get("/executions/ghost/timeline", headers=self.headers)
        self.assertEqual(resp.status_code, 404)

    def test_verify_intact_chain(self):
        resp = self.client.get("/executions/run-1/audit/verify", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["checked"], 5)
        self.assertIsNone(body["first_broken_seq"])

    def test_prometheus_endpoint_exposes_the_scrape_format(self):
        resp = self.client.get("/metrics/prometheus", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/plain"))
        body = resp.text
        self.assertIn("asa_runs_total 1", body)
        self.assertIn("asa_runs_succeeded_total 0", body)
        self.assertIn("asa_runs_failed_total 1", body)
        self.assertIn("asa_step_retries_total 1", body)
        self.assertIn('asa_memory_items{state="QUARANTINED"} 0', body)
        self.assertIn('asa_build_info{environment="development",version="0.1.0"} 1', body)
        self.assertTrue(body.endswith("\n"))

    def test_prometheus_endpoint_requires_audit_read(self):
        self.assertEqual(self.client.get("/metrics/prometheus").status_code, 401)
        # VIEWER is granted audit:read (the matrix in app/roles.py), so a
        # scrape is a read like any other; only an anonymous caller is denied.
        viewer = create_token("viewer", SECRET, 10, role="VIEWER")
        resp = self.client.get("/metrics/prometheus", headers={"Authorization": f"Bearer {viewer}"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("asa_runs_total 1", resp.text)

    def test_verify_names_the_first_tampered_record(self):
        with sqlite3.connect(self.settings.sqlite_path) as conn:
            conn.execute(
                "UPDATE audit_events SET payload = ? WHERE execution_id = ? AND seq = 2",
                ('{"n": 999}', "run-1"),
            )
        body = self.client.get("/executions/run-1/audit/verify", headers=self.headers).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["first_broken_seq"], 2)
        self.assertIn("content", body["reason"])

    def test_verify_detects_a_missing_record(self):
        with sqlite3.connect(self.settings.sqlite_path) as conn:
            conn.execute(
                "DELETE FROM audit_events WHERE execution_id = ? AND seq = ?", ("run-1", 1)
            )
        body = self.client.get("/executions/run-1/audit/verify", headers=self.headers).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["first_broken_seq"], 2)

    def test_verify_requires_authentication(self):
        self.assertEqual(self.client.get("/executions/run-1/audit/verify").status_code, 401)

    def test_metrics_endpoint_aggregates_executions(self):
        resp = self.client.get("/metrics/executions", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["retries"], 1)  # step 'b' reached attempt 2

    def test_metrics_endpoint_requires_auth(self):
        self.assertEqual(self.client.get("/metrics/executions").status_code, 401)


if __name__ == "__main__":
    unittest.main()
