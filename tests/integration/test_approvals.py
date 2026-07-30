"""Phase 3 item [35]: approval quorum, expiration and final decision (FR-08).

Covers the approval state machine: requests pin a quorum and deadline,
distinct approvers count toward the quorum, a rejection resolves the request
immediately, expired requests cannot be decided, and a sweep retires them.
"""

import tempfile
import unittest
from pathlib import Path

from app.approvals import (
    APPROVED,
    CANCELLED,
    EXPIRED,
    PENDING,
    REJECTED,
    ApprovalError,
    ApprovalService,
)
from app.db import init_db
from app.models import ExecutionStore, WorkflowStore

NOW = "2026-01-01T00:00:00+00:00"
LATER = "2026-01-01T02:00:00+00:00"


class ApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        workflow_id = WorkflowStore(self.db).ensure_workflow("w")
        self.execution_id = ExecutionStore(self.db).start(workflow_id, 1)["id"]
        self.service = ApprovalService(self.db)

    def _request(self, node_id="gate", quorum=1, ttl=3600):
        return self.service.request(
            self.execution_id, node_id, quorum=quorum, ttl_seconds=ttl, now=NOW
        )

    def test_request_starts_pending_with_quorum_and_deadline(self):
        request = self._request(quorum=2)
        self.assertEqual(request.status, PENDING)
        self.assertEqual(request.quorum, 2)
        self.assertTrue(request.expires_at)

    def test_request_is_idempotent_per_node(self):
        first = self._request()
        second = self._request(quorum=5)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.quorum, 1)  # original quorum is kept

    def test_single_approver_resolves_a_quorum_of_one(self):
        self._request(quorum=1)
        result = self.service.decide(
            self.execution_id, "gate", approver="alice", verdict="APPROVED", now=NOW
        )
        self.assertEqual(result.status, APPROVED)

    def test_quorum_of_two_needs_two_distinct_approvers(self):
        self._request(quorum=2)
        first = self.service.decide(
            self.execution_id, "gate", approver="alice", verdict="APPROVED", now=NOW
        )
        self.assertEqual(first.status, PENDING)
        second = self.service.decide(
            self.execution_id, "gate", approver="bob", verdict="APPROVED", now=NOW
        )
        self.assertEqual(second.status, APPROVED)

    def test_same_approver_does_not_count_twice(self):
        self._request(quorum=2)
        self.service.decide(
            self.execution_id, "gate", approver="alice", verdict="APPROVED", now=NOW
        )
        again = self.service.decide(
            self.execution_id, "gate", approver="alice", verdict="APPROVED", now=NOW
        )
        self.assertEqual(again.status, PENDING)

    def test_any_rejection_resolves_immediately(self):
        self._request(quorum=3)
        result = self.service.decide(
            self.execution_id,
            "gate",
            approver="mallory",
            verdict="REJECTED",
            reason="risky",
            now=NOW,
        )
        self.assertEqual(result.status, REJECTED)

    def test_deciding_after_resolution_is_rejected(self):
        self._request(quorum=1)
        self.service.decide(
            self.execution_id, "gate", approver="alice", verdict="APPROVED", now=NOW
        )
        with self.assertRaises(ApprovalError):
            self.service.decide(
                self.execution_id, "gate", approver="bob", verdict="APPROVED", now=NOW
            )

    def test_expired_request_cannot_be_decided(self):
        self._request(ttl=60)
        with self.assertRaises(ApprovalError):
            self.service.decide(
                self.execution_id, "gate", approver="alice", verdict="APPROVED", now=LATER
            )
        self.assertEqual(self.service.status(self.execution_id, "gate"), EXPIRED)

    def test_sweep_expired_retires_overdue_requests(self):
        gate = self._request(ttl=60)
        other = self.service.request(
            self.execution_id, "gate2", quorum=1, ttl_seconds=86400, now=NOW
        )
        expired = self.service.sweep_expired(now=LATER)
        self.assertEqual([r.id for r in expired], [gate.id])
        self.assertEqual(self.service.status(self.execution_id, "gate"), EXPIRED)
        self.assertEqual(self.service.get(self.execution_id, "gate2").id, other.id)
        self.assertEqual(self.service.status(self.execution_id, "gate2"), PENDING)

    def test_cancel_pending_request(self):
        self._request()
        cancelled = self.service.cancel(self.execution_id, "gate")
        self.assertEqual(cancelled.status, CANCELLED)

    def test_unknown_request_raises(self):
        with self.assertRaises(KeyError):
            self.service.get(self.execution_id, "ghost")

    def test_bad_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            self.service.request(self.execution_id, "g", quorum=0)
        self._request(node_id="g")
        with self.assertRaises(ValueError):
            self.service.decide(
                self.execution_id, "g", approver="a", verdict="maybe", now=NOW
            )

    def test_decisions_are_queryable(self):
        self._request(quorum=2)
        self.service.decide(
            self.execution_id, "gate", approver="alice", verdict="APPROVED", now=NOW
        )
        rows = self.service.decisions(self.execution_id, "gate")
        self.assertEqual([r["decided_by"] for r in rows], ["alice"])

    def test_list_filters_by_execution(self):
        self._request()
        self.assertEqual(len(self.service.list_requests(self.execution_id)), 1)
        self.assertEqual(self.service.list_requests("other"), [])


if __name__ == "__main__":
    unittest.main()
