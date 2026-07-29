"""Phase 3 item [28]: execution and execution-step persistence (FR-05, FR-10).

Every execution pins one ``(workflow_id, version)``; each node attempt is a
durable ``execution_steps`` row keyed by ``(execution_id, node_id, attempt)``
so re-recording an attempt updates instead of duplicating.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from app.models import ExecutionStore, WorkflowStore
from engine.executor.result import NodeResult, RunResult
from engine.executor.scheduler import run_dag
from engine.executor.states import BLOCKED, FAILED, SUCCEEDED


def _run(coro):
    return asyncio.run(coro)


class ExecutionStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.workflow_id = WorkflowStore(self.db).ensure_workflow("runs")
        self.store = ExecutionStore(self.db)

    def test_start_pins_workflow_and_version(self):
        execution = self.store.start(self.workflow_id, 7, triggered_by="file.arrived")
        self.assertEqual(execution["status"], "RUNNING")
        self.assertEqual(execution["workflow_id"], self.workflow_id)
        self.assertEqual(execution["version"], 7)
        self.assertEqual(execution["triggered_by"], "file.arrived")
        self.assertEqual(execution["finished_at"], "")

    def test_run_id_pins_the_execution_id(self):
        execution = self.store.start(self.workflow_id, 1, run_id="run-abc")
        self.assertEqual(execution["id"], "run-abc")

    def test_finish_marks_terminal(self):
        execution = self.store.start(self.workflow_id, 1)
        self.store.finish(execution["id"], "COMPLETED")
        stored = self.store.get(execution["id"])
        self.assertEqual(stored["status"], "COMPLETED")
        self.assertTrue(stored["finished_at"])

    def test_record_step_is_idempotent_per_attempt(self):
        execution = self.store.start(self.workflow_id, 1)
        self.store.record_step(execution["id"], "a", "RUNNING", attempt=1)
        self.store.record_step(execution["id"], "a", "SUCCEEDED", attempt=1, output_summary="ok")
        steps = self.store.list_steps(execution["id"])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["state"], "SUCCEEDED")
        self.assertEqual(steps[0]["output_summary"], "ok")

    def test_retry_attempts_are_separate_rows(self):
        execution = self.store.start(self.workflow_id, 1)
        self.store.record_step(execution["id"], "a", "RETRYING", attempt=1, error="boom")
        self.store.record_step(execution["id"], "a", "SUCCEEDED", attempt=2)
        attempts = [s["attempt"] for s in self.store.list_steps(execution["id"])]
        self.assertEqual(attempts, [1, 2])

    def test_get_unknown_execution_raises(self):
        with self.assertRaises(KeyError):
            self.store.get("nope")

    def test_record_run_persists_executor_result(self):
        async def handler(nid, spec):
            if nid == "b":
                raise RuntimeError("install killed")
            return {"done": nid}

        dag = {"nodes": {"a": {}, "b": {}, "c": {}}, "edges": [("a", "b"), ("b", "c")]}
        run = _run(run_dag(dag, handler, timeout_s=5))
        self.assertEqual(run.status, "FAILED")

        stored = self.store.record_run(self.workflow_id, 3, run, triggered_by="manual")
        self.assertEqual(stored["status"], "FAILED")
        self.assertEqual(stored["version"], 3)
        states = {s["node_id"]: s["state"] for s in stored["steps"]}
        self.assertEqual(states["a"], SUCCEEDED)
        self.assertEqual(states["b"], FAILED)
        self.assertEqual(states["c"], BLOCKED)

    def test_output_summary_is_bounded(self):
        run = RunResult(run_id="r1", status="COMPLETED")
        run.nodes["a"] = NodeResult(node_id="a", status="OK", output={"blob": "x" * 5000})
        run.order = ["a"]
        stored = self.store.record_run(self.workflow_id, 1, run)
        summary = stored["steps"][0]["output_summary"]
        self.assertLessEqual(len(summary), 500)


if __name__ == "__main__":
    unittest.main()
