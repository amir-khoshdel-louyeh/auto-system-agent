"""Phase 5 item [48]: record policy/approval/dispatch/retry/result/replay.

The AuditTrail writes each FR-12 event category onto one execution's chain,
and its sink plugs straight into the scheduler so a real run is audited.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from engine.executor.scheduler import run_dag
from safety.policy_service import PolicyService
from storage.audit import (
    APPROVAL_DECISION,
    NODE_DISPATCHED,
    NODE_RESULT,
    NODE_RETRY,
    POLICY_DECISION,
    RUN_FINISHED,
    RUN_REPLAY,
    RUN_STARTED,
    AuditStore,
    AuditTrail,
)


def _run(coro):
    return asyncio.run(coro)


class AuditTrailTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.store = AuditStore(self.db)
        self.trail = AuditTrail(self.store, "run-1")

    def test_records_every_event_category(self):
        self.trail.run_started("demo", 3, trigger="file.arrived", run_id="run-1")
        self.trail.policy("a", PolicyService().decide("a", {"type": "shell", "run": "echo hi"}))
        self.trail.approval("gate", "APPROVED", decided_by="alice")
        self.trail.dispatch("a", run_id="run-1")
        self.trail.retry("a", 1, error="transient")
        self.trail.result("a", "SUCCEEDED", output_summary="{}")
        self.trail.replay("run-0", "run-1-b", match=True)
        self.trail.run_finished("COMPLETED")

        names = [row["event"] for row in self.store.events("run-1")]
        for expected in (
            RUN_STARTED,
            POLICY_DECISION,
            APPROVAL_DECISION,
            NODE_DISPATCHED,
            NODE_RETRY,
            NODE_RESULT,
            RUN_REPLAY,
            RUN_FINISHED,
        ):
            self.assertIn(expected, names)
        self.assertTrue(self.store.verify("run-1"))

    def test_policy_event_carries_verdict_and_version(self):
        self.trail.policy("a", PolicyService().decide("a", {"type": "shell", "run": "rm -rf /"}))
        row = [r for r in self.store.events("run-1") if r["event"] == POLICY_DECISION][0]
        self.assertIn('"verdict": "DENY"', row["payload"])
        self.assertIn("policy_version", row["payload"])

    def test_sink_audits_a_real_scheduler_run(self):
        async def handler(nid, spec):
            if nid == "b":
                raise RuntimeError("boom")
            return {"ok": nid}

        dag = {"nodes": {"a": {}, "b": {}}, "edges": [("a", "b")]}
        result = _run(run_dag(dag, handler, timeout_s=5, on_event=self.trail.sink()))
        self.assertEqual(result.status, "FAILED")
        names = [row["event"] for row in self.store.events("run-1")]
        self.assertIn("run.started", names)
        self.assertIn("run.finished", names)
        self.assertIn("node.started", names)
        self.assertTrue(self.store.verify("run-1"))

    def test_output_summary_is_bounded(self):
        self.trail.result("a", "SUCCEEDED", output_summary="x" * 5000)
        row = [r for r in self.store.events("run-1") if r["event"] == NODE_RESULT][0]
        self.assertLess(len(row["payload"]), 3000, "audit payloads stay bounded (FR-11)")


if __name__ == "__main__":
    unittest.main()
