"""Phase 5 items [46]-[48]: the run path and approvals audit themselves.

Acceptance target §3.2: *every execution and approval has an auditable
hash-chain record*. These tests drive a real bus run and a real approval
decision and then verify the execution's chain, so the FR-12 vocabulary is
written by the product, not only by a unit test.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.approvals import ApprovalService
from app.db import init_db
from bus.backend import MemoryBackend
from bus.workers.shell import make_shell_worker
from storage.audit import (
    APPROVAL_DECISION,
    NODE_DISPATCHED,
    NODE_RESULT,
    POLICY_DECISION,
    RUN_FINISHED,
    RUN_STARTED,
    AuditStore,
)

WORKFLOW = """workflow: audited-run
version: 1
triggers:
  - manual: true
nodes:
  - id: scaffold
    type: shell
    run: mkdir -p demo-app
  - id: wipe
    type: shell
    run: rm -f /etc/passwd
  - id: readme
    type: shell
    run: printf '# demo-app' > demo-app/README.md
edges: [[scaffold, wipe]]
"""


async def _pump(worker, stop):
    while not stop.is_set():
        stats = await worker.drain_once()
        if stats.processed == 0 and stats.errors == 0:
            await asyncio.sleep(0.01)


async def _run(dsl_text, workflows_dir, audit_store, run_id):
    from bus.workers.dispatcher import run_workflow

    backend = MemoryBackend()
    worker = make_shell_worker(backend, "audited", exec_fn=lambda nid, node, ctx: _ok(nid))
    stop = asyncio.Event()
    pump = asyncio.create_task(_pump(worker, stop))
    try:
        return await run_workflow(
            backend,
            "audited-run",
            workflows_dir=workflows_dir,
            run_id=run_id,
            trigger="manual",
            dsl_text=dsl_text,
            audit_store=audit_store,
        )
    finally:
        stop.set()
        await pump


async def _ok(node_id):
    return {"ok": node_id}


class RunPathAuditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db = init_db(self.dir / "app.db")
        self.audit = AuditStore(self.db)

    def test_dangerous_workflow_node_is_denied_and_audited(self):
        # The static validator accepts this command (its danger list is a
        # subset); the runtime policy gate is the authority and denies it.
        run = asyncio.run(_run(WORKFLOW, self.dir, self.audit, "run-a"))
        # wipe is DENY; readme depends on it, so the run fails safely.
        self.assertEqual(run.status, "FAILED")
        self.assertEqual(run.nodes["wipe"].status, "FAILED")
        self.assertIn("policy DENY", run.nodes["wipe"].error)

        events = [row["event"] for row in self.audit.events("run-a")]
        self.assertIn(RUN_STARTED, events)
        self.assertIn(POLICY_DECISION, events)
        self.assertIn(RUN_FINISHED, events)

        denials = [
            row
            for row in self.audit.events("run-a")
            if row["event"] == POLICY_DECISION and '"verdict": "DENY"' in row["payload"]
        ]
        self.assertTrue(denials, "the denied node must be on the chain")
        self.assertIn('"node_id": "wipe"', denials[0]["payload"])

    def test_run_chain_records_dispatch_result_and_verifies(self):
        workflow = WORKFLOW.replace(
            "  - id: wipe\n    type: shell\n    run: rm -f /etc/passwd\n", ""
        ).replace("edges: [[scaffold, wipe]]", "edges: [[scaffold, readme]]")
        run = asyncio.run(_run(workflow, self.dir, self.audit, "run-b"))
        self.assertEqual(run.status, "COMPLETED")

        events = [row["event"] for row in self.audit.events("run-b")]
        for expected in (RUN_STARTED, POLICY_DECISION, NODE_DISPATCHED, NODE_RESULT, RUN_FINISHED):
            self.assertIn(expected, events)
        verification = self.audit.verify_chain("run-b")
        self.assertTrue(verification.ok, verification.reason)
        self.assertGreaterEqual(verification.checked, 6)

    def test_run_without_an_audit_store_writes_no_chain(self):
        asyncio.run(_run(WORKFLOW, self.dir, None, "run-c"))
        self.assertEqual(self.audit.events("run-c"), [])

    def test_failed_run_is_recorded_as_finished(self):
        asyncio.run(_run(WORKFLOW, self.dir, self.audit, "run-d"))
        finished = [r for r in self.audit.events("run-d") if r["event"] == RUN_FINISHED][0]
        self.assertIn('"status": "FAILED"', finished["payload"])


class ApprovalAuditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.audit = AuditStore(self.db)
        self.service = ApprovalService(self.db, audit=self.audit)

    def test_decision_is_appended_to_the_execution_chain(self):
        self.service.request("run-1", "gate", quorum=1)
        self.service.decide("run-1", "gate", approver="alice", verdict="APPROVED", reason="ok")
        rows = [r for r in self.audit.events("run-1") if r["event"] == APPROVAL_DECISION]
        self.assertEqual(len(rows), 1)
        self.assertIn('"status": "APPROVED"', rows[0]["payload"])
        self.assertIn('"decided_by": "alice"', rows[0]["payload"])
        self.assertTrue(self.audit.verify("run-1"))

    def test_expiry_is_audited(self):
        self.service.request(
            "run-2", "gate", quorum=1, ttl_seconds=0, now="2026-01-01T00:00:00+00:00"
        )
        expired = self.service.sweep_expired(now="2026-01-01T00:10:00+00:00")
        self.assertEqual(len(expired), 1)
        rows = [r for r in self.audit.events("run-2") if r["event"] == APPROVAL_DECISION]
        self.assertIn('"status": "EXPIRED"', rows[0]["payload"])

    def test_service_without_audit_store_stays_silent(self):
        plain = ApprovalService(self.db)
        plain.request("run-3", "gate", quorum=1)
        plain.decide("run-3", "gate", approver="bob", verdict="APPROVED")
        self.assertEqual(self.audit.events("run-3"), [])

    def test_low_quorum_decision_stays_pending_on_the_chain(self):
        self.service.request("run-4", "gate", quorum=2)
        self.service.decide("run-4", "gate", approver="alice", verdict="APPROVED")
        rows = [r for r in self.audit.events("run-4") if r["event"] == APPROVAL_DECISION]
        self.assertIn('"status": "PENDING"', rows[0]["payload"])


if __name__ == "__main__":
    unittest.main()
