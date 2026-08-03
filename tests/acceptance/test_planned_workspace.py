"""Phase 6 gate + W1: a natural-language request to audited execution.

W1 walk-through with the offline planner in the loop:

1. the request becomes a typed multi-step plan;
2. the plan compiles to a DAG and is accepted by the shared validator;
3. independent folder nodes are on the same level (concurrent dispatch);
4. every node is policy-decided and the decisions are on the audit chain;
5. the run completes over the bus and persists its step results;
6. run and step records are queryable in the hash chain;
7. the run replays from its pinned version;
8. normalized original and replay outputs match.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from app.models import ExecutionStore, WorkflowStore
from app.replay import ReplayService
from bus.backend import MemoryBackend
from bus.workers.dispatcher import run_workflow
from bus.workers.shell import make_shell_worker
from engine.executor.scheduler import compute_levels
from orchestration.offline_planner import OfflinePlanner
from orchestration.plan_compiler import compile_plan
from storage.audit import NODE_RESULT, POLICY_DECISION, RUN_FINISHED, RUN_STARTED, AuditStore

REQUEST = "create a workspace called demo-app with three folders and a README"


async def _pump(worker, stop):
    while not stop.is_set():
        stats = await worker.drain_once()
        if stats.processed == 0 and stats.errors == 0:
            await asyncio.sleep(0.01)


class PlannedWorkspaceAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db = init_db(self.dir / "app.db")
        self.audit = AuditStore(self.db)
        self.workflows = WorkflowStore(self.db)
        self.executions = ExecutionStore(self.db)
        self.plan = OfflinePlanner().plan(REQUEST)
        self.workflow = compile_plan(self.plan, name="onboarding-demo")
        self.document = self.plan.to_document("onboarding-demo", version=1)
        self.dsl_text = json.dumps(self.document)

    # -- helpers ---------------------------------------------------------
    async def _run_async(self, *, run_id: str, dsl_text: str | None = None, exec_fn=None):
        backend = MemoryBackend()
        worker = make_shell_worker(backend, "w1", exec_fn=exec_fn)
        stop = asyncio.Event()
        pump = asyncio.create_task(_pump(worker, stop))
        try:
            return await run_workflow(
                backend,
                "onboarding-demo",
                workflows_dir=self.dir,
                run_id=run_id,
                trigger="manual",
                dsl_text=dsl_text or self.dsl_text,
                audit_store=self.audit,
            )
        finally:
            stop.set()
            await pump

    def _run(self, *, run_id: str, dsl_text: str | None = None, exec_fn=None):
        return asyncio.run(self._run_async(run_id=run_id, dsl_text=dsl_text, exec_fn=exec_fn))

    # -- 1-3: request -> plan -> validated DAG ---------------------------
    def test_request_becomes_a_typed_plan(self):
        self.assertEqual(self.plan.intent, "create_workspace")
        self.assertEqual(self.plan.slots["workspace_name"], "demo-app")
        self.assertTrue(self.plan.executable)
        self.assertGreaterEqual(self.plan.confidence, 0.75)

    def test_plan_compiles_to_a_validated_dag_with_parallel_folders(self):
        levels = compute_levels(
            {node.id: node for node in self.workflow.nodes}, list(self.workflow.edges)
        )
        self.assertEqual(levels[0], ["create-demo-app"])
        self.assertEqual(len(levels[1]), 4, "three folders + README dispatch together")
        self.assertIn("write-readme", levels[1])

    # -- 4-6: execution, policy, audit ----------------------------------
    def test_run_completes_and_is_audited(self):
        run = self._run(run_id="w1-planned")
        self.assertEqual(run.status, "COMPLETED")

        stored = self.executions.record_run(
            self.workflows.ensure_workflow("onboarding-demo"), 1, run, triggered_by="manual"
        )
        self.assertEqual(stored["status"], "COMPLETED")
        states = [step["state"] for step in stored["steps"]]
        self.assertEqual(set(states), {"SUCCEEDED"})

        events = [row["event"] for row in self.audit.events("w1-planned")]
        self.assertIn(RUN_STARTED, events)
        self.assertIn(POLICY_DECISION, events)
        self.assertIn(NODE_RESULT, events)
        self.assertIn(RUN_FINISHED, events)
        self.assertTrue(self.audit.verify("w1-planned"))

        decided = [
            row["payload"]
            for row in self.audit.events("w1-planned")
            if row["event"] == POLICY_DECISION
        ]
        self.assertEqual(len(decided), len(self.plan.steps), "every node is decided")
        self.assertTrue(all('"verdict": "ALLOW"' in payload for payload in decided))

    # -- 7-8: replay ----------------------------------------------------
    def test_run_replays_from_its_pinned_version_and_matches(self):
        run = self._run(run_id="w1-source")
        self.assertEqual(run.status, "COMPLETED")
        workflow_id = self.workflows.ensure_workflow("onboarding-demo")
        self.workflows.publish_version(workflow_id, 1, self.dsl_text)
        self.executions.record_run(workflow_id, 1, run, triggered_by="manual")

        service = ReplayService(self.workflows, self.executions, self.audit)

        async def runner(dsl_text: str, replay_id: str):
            return await self._run_async(run_id=replay_id, dsl_text=dsl_text)

        result = asyncio.run(service.replay("w1-source", runner, role="OPERATOR"))

        self.assertEqual(result.version, 1)
        self.assertEqual(result.status, "COMPLETED")
        self.assertTrue(result.comparison.match)
        self.assertEqual(result.comparison.compared, len(self.plan.steps))
        self.assertTrue(self.audit.verify(result.replay_execution_id))
        payload = self.audit.events("w1-source")[-1]
        self.assertEqual(payload["event"], "run.replay")


if __name__ == "__main__":
    unittest.main()
