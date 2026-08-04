"""Phase 8 item [77]/[80]: the workbench walkthrough, without a display.

The view is the whole behavior of the four screens: it calls one endpoint per
action and formats the report into the lines a listbox shows. Driving it with
a scripted fake client proves the walkthrough — validate, dry run, open a
run, decide an approval, inspect and verify the audit chain — in CI where no
X server exists.
"""

import unittest

from ui.workbench import APPROVE, REJECT, WorkbenchView

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


class FakeApi:
    """Scripted client that records calls and returns fixed reports."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.valid = True
        self.dry_run_report = {
            "valid": True,
            "name": "demo",
            "version": 1,
            "nodes": [
                {
                    "node_id": "read",
                    "node_type": "shell",
                    "verdict": "ALLOW",
                    "explanation": {"headline": "Allowed — low risk (score 0/100)."},
                }
            ],
            "errors": [],
            "counts": {"ALLOW": 1, "NEED_APPROVAL": 0, "DENY": 0},
            "safe_to_run": True,
            "persisted": False,
            "dispatched": False,
        }
        self.decision_status = "PENDING"

    def validate(self, dsl, dsl_type="yaml"):
        self.calls.append(("validate", dsl, dsl_type))
        if self.valid:
            return {"valid": True, "name": "demo", "version": 1, "errors": []}
        return {
            "valid": False,
            "name": "",
            "version": 0,
            "errors": [{"stage": "schema", "line": 4, "column": 3, "message": "bad type"}],
        }

    def dry_run(self, dsl, *, dsl_type="yaml", workspace_root=""):
        self.calls.append(("dry_run", workspace_root))
        return self.dry_run_report

    def executions(self, limit=50):
        self.calls.append(("executions", limit))
        return {
            "total": 1,
            "executions": [
                {
                    "id": "abcdef0123456789",
                    "workflow_id": "wf1",
                    "version": 3,
                    "status": "COMPLETED",
                    "triggered_by": "manual",
                    "started_at": "2026-10-17T10:00:00+00:00",
                    "finished_at": "2026-10-17T10:00:02+00:00",
                    "error": "",
                }
            ],
        }

    def timeline(self, execution_id):
        self.calls.append(("timeline", execution_id))
        return {
            "execution_id": execution_id,
            "workflow_id": "wf1",
            "version": 3,
            "status": "COMPLETED",
            "started_at": "2026-10-17T10:00:00+00:00",
            "steps": [
                {"node_id": "read", "state": "SUCCEEDED", "attempt": 1, "error": ""},
                {"node_id": "gate", "state": "FAILED", "attempt": 2, "error": "boom"},
            ],
        }

    def approvals(self, execution_id=""):
        self.calls.append(("approvals", execution_id))
        return {
            "total": 1,
            "requests": [
                {
                    "id": "req1",
                    "execution_id": "abcdef0123456789",
                    "node_id": "deploy",
                    "quorum": 2,
                    "status": "PENDING",
                    "expires_at": "2026-10-17T11:00:00+00:00",
                    "created_at": "2026-10-17T10:00:00+00:00",
                }
            ],
        }

    def decide(self, execution_id, node_id, verdict, *, reason=""):
        self.calls.append(("decide", execution_id, node_id, verdict, reason))
        return {
            "request": {
                "id": "req1",
                "execution_id": execution_id,
                "node_id": node_id,
                "quorum": 2,
                "status": self.decision_status,
                "expires_at": "",
                "created_at": "",
            }
        }

    def audit(self, execution_id, *, limit=100):
        self.calls.append(("audit", execution_id, limit))
        return {
            "execution_id": execution_id,
            "total": 2,
            "limit": limit,
            "offset": 0,
            "events": [
                {"seq": 1, "event": "run.started", "payload": "{}", "hash": "a" * 64},
                {"seq": 2, "event": "policy.decision", "payload": "{}", "hash": "b" * 64},
            ],
        }

    def verify(self, execution_id):
        self.calls.append(("verify", execution_id))
        return {"ok": True, "checked": 2, "first_broken_seq": None, "reason": ""}


class WorkflowScreenTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.view = WorkbenchView(self.api)

    def test_valid_workflow_shows_one_ok_line(self):
        lines = self.view.validate(DSL)
        self.assertEqual(lines, ["OK demo v1"])
        self.assertEqual(self.view.status, "valid")
        self.assertEqual(self.api.calls, [("validate", DSL, "yaml")])

    def test_invalid_workflow_shows_stage_line_column(self):
        self.api.valid = False
        lines = self.view.validate(DSL)
        self.assertEqual(lines, ["schema:4:3 — bad type"])
        self.assertEqual(self.view.status, "invalid")

    def test_dry_run_lists_each_node_and_the_inert_summary(self):
        lines = self.view.dry_run(DSL)
        self.assertIn("read [ALLOW] Allowed — low risk (score 0/100).", lines)
        summary = lines[-1]
        self.assertIn("safe_to_run=True", summary)
        self.assertIn("persisted=False", summary)
        self.assertIn("dispatched=False", summary)

    def test_dry_run_marks_a_blocked_workflow(self):
        self.api.dry_run_report = {
            **self.api.dry_run_report,
            "safe_to_run": False,
            "counts": {"ALLOW": 0, "NEED_APPROVAL": 0, "DENY": 1},
            "nodes": [
                {
                    "node_id": "wipe",
                    "node_type": "shell",
                    "verdict": "DENY",
                    "explanation": {"headline": "Denied — high risk (score 85/100)."},
                }
            ],
        }
        lines = self.view.dry_run(DSL)
        self.assertIn("wipe [DENY] Denied — high risk (score 85/100).", lines)
        self.assertEqual(self.view.status, "blocked")


class RunScreenTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.view = WorkbenchView(self.api)

    def test_run_list_remembers_ids_and_formats_rows(self):
        lines = self.view.runs()
        self.assertEqual(self.view.run_ids, ["abcdef0123456789"])
        self.assertIn("COMPLETED", lines[0])
        self.assertIn("abcdef01", lines[0])

    def test_timeline_shows_each_attempt_and_its_error(self):
        lines = self.view.timeline("abcdef0123456789")
        self.assertIn("COMPLETED wf1 v3", lines[0])
        self.assertTrue(any("read [SUCCEEDED] attempt 1" in line for line in lines))
        self.assertTrue(any("gate [FAILED] attempt 2 — boom" in line for line in lines))


class ApprovalScreenTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.view = WorkbenchView(self.api)

    def test_approval_list_shows_quorum_and_keeps_ids(self):
        lines = self.view.approvals()
        self.assertEqual(self.view.approval_ids, [("abcdef0123456789", "deploy")])
        self.assertIn("PENDING", lines[0])
        self.assertIn("quorum 2", lines[0])

    def test_approve_sends_the_decision_and_reports_the_status(self):
        self.view.approvals()
        self.api.decision_status = "APPROVED"
        message = self.view.decide("abcdef0123456789", "deploy", APPROVE, reason="looks fine")
        self.assertIn("APPROVED", message)
        self.assertIn(
            ("decide", "abcdef0123456789", "deploy", APPROVE, "looks fine"), self.api.calls
        )

    def test_reject_is_passed_through(self):
        self.api.decision_status = "REJECTED"
        message = self.view.decide("abcdef0123456789", "deploy", REJECT)
        self.assertIn("REJECTED", message)
        self.assertEqual(self.api.calls[-1][3], REJECT)

    def test_unknown_verdict_is_refused_before_calling_the_api(self):
        with self.assertRaises(ValueError):
            self.view.decide("abcdef0123456789", "deploy", "MAYBE")
        self.assertEqual(self.api.calls, [])


class AuditScreenTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.view = WorkbenchView(self.api)

    def test_audit_page_renders_events_in_order(self):
        lines = self.view.audit("abcdef0123456789")
        self.assertIn("2 event(s)", lines[0])
        self.assertIn("#1 run.started", lines[1])
        self.assertIn("#2 policy.decision", lines[2])
        self.assertIn("hash aaaaaaaaaaaa", lines[1])

    def test_verify_reports_success(self):
        self.assertIn("chain OK — 2 event(s) verified", self.view.verify("abcdef0123456789"))

    def test_verify_reports_the_first_broken_link(self):
        self.api.verify = lambda execution_id: {
            "ok": False,
            "checked": 2,
            "first_broken_seq": 2,
            "reason": "hash mismatch",
        }
        self.assertEqual(
            self.view.verify("abcdef0123456789"),
            "chain BROKEN at seq 2: hash mismatch",
        )


class WalkthroughTests(unittest.TestCase):
    def test_full_walkthrough_touches_every_screen(self):
        api = FakeApi()
        view = WorkbenchView(api)
        self.assertEqual(view.validate(DSL), ["OK demo v1"])
        self.assertTrue(view.dry_run(DSL)[-1].startswith("summary:"))
        view.runs()
        view.timeline(view.run_ids[0])
        view.approvals()
        view.decide(*view.approval_ids[0], APPROVE)
        view.audit(view.run_ids[0])
        self.assertIn("chain OK", view.verify(view.run_ids[0]))
        self.assertEqual(
            [call[0] for call in api.calls],
            [
                "validate",
                "dry_run",
                "executions",
                "timeline",
                "approvals",
                "decide",
                "audit",
                "verify",
            ],
        )


if __name__ == "__main__":
    unittest.main()
