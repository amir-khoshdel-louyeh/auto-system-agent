"""Phase 5 item [52]: replay with source-version pinning (FR-13).

Replay re-executes the *immutable* workflow version the original run pinned,
never the mutable YAML file, gives the rerun its own correlation id, keeps
the source execution untouched, and records a ``run.replay`` audit event on
the source chain. A modified pinned version is refused.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from app.models import ExecutionStore, WorkflowStore
from app.replay import ReplayError, ReplayService
from engine.dsl.compiler import compile as compile_wf
from engine.dsl.loader import load_text
from engine.executor.result import NodeResult, RunResult
from engine.executor.scheduler import run_dag
from storage.audit import AuditStore

V1 = """workflow: replay-demo
version: 1
triggers:
  - manual: true
nodes:
  - id: a
    type: shell
    run: echo v1-a
  - id: b
    type: shell
    run: echo v1-b
edges: [[a, b]]
"""

V2 = """workflow: replay-demo
version: 2
triggers:
  - manual: true
nodes:
  - id: a
    type: shell
    run: echo v2-a
edges: []
"""


def make_run(run_id: str, outputs: dict[str, dict], *, status: str = "COMPLETED") -> RunResult:
    """Build the executor result a run would have produced."""
    run = RunResult(run_id=run_id, status=status)
    for node_id, output in outputs.items():
        run.nodes[node_id] = NodeResult(node_id=node_id, status="OK", output=output, attempts=1)
        run.order.append(node_id)
    return run


class ReplayServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.workflows = WorkflowStore(self.db)
        self.executions = ExecutionStore(self.db)
        self.audit = AuditStore(self.db)
        self.workflow_id = self.workflows.ensure_workflow("replay-demo")
        self.workflows.publish_version(self.workflow_id, 1, V1)
        self.source = self.executions.record_run(
            self.workflow_id,
            1,
            make_run("source-run", {"a": {"echo": "v1-a"}, "b": {"echo": "v1-b"}}),
            triggered_by="file.arrived",
        )
        self.service = ReplayService(self.workflows, self.executions, self.audit)
        self.received: list[str] = []

    def _runner(self, outputs: dict[str, dict] | None = None):
        """A runner that executes the *pinned text* it is handed."""

        async def runner(dsl_text: str, replay_id: str) -> RunResult:
            self.received.append(dsl_text)
            dag = compile_wf(load_text(dsl_text))

            async def handler(node_id: str, node: dict) -> dict:
                if outputs is not None:
                    return outputs.get(node_id, {})
                return {"echo": node["params"]["run"].replace("echo ", "")}

            return await run_dag(dag, handler, run_id=replay_id)

        return runner

    def _replay(self, *, role="OWNER", replay_id="replay-run-1", outputs=None):
        return asyncio.run(
            self.service.replay(
                self.source["id"],
                self._runner(outputs),
                role=role,
                replay_id=replay_id,
            )
        )

    def test_replays_the_pinned_version_not_a_newer_document(self):
        self.workflows.publish_version(self.workflow_id, 2, V2)
        result = self._replay()
        self.assertEqual(self.received, [V1])
        self.assertEqual(result.version, 1)
        self.assertEqual(result.workflow_id, self.workflow_id)

    def test_replay_gets_a_new_correlation_id(self):
        result = self._replay()
        self.assertEqual(result.replay_execution_id, "replay-run-1")
        self.assertNotEqual(result.replay_execution_id, self.source["id"])
        stored = self.executions.get("replay-run-1")
        self.assertEqual(stored["triggered_by"], "replay")
        self.assertEqual(stored["version"], 1)

    def test_source_execution_is_left_untouched(self):
        self._replay()
        source = self.executions.get(self.source["id"])
        self.assertEqual(source["status"], "COMPLETED")
        self.assertEqual(source["triggered_by"], "file.arrived")
        self.assertEqual(len(source["steps"]), 2)

    def test_matching_outputs_report_a_match(self):
        result = self._replay()
        self.assertEqual(result.status, "COMPLETED")
        self.assertTrue(result.comparison.match)
        self.assertEqual(result.comparison.compared, 2)
        self.assertEqual(result.comparison.mismatched, [])

    def test_mismatched_output_names_the_node(self):
        result = self._replay(outputs={"a": {"echo": "v1-a"}, "b": {"echo": "changed"}})
        self.assertFalse(result.comparison.match)
        self.assertEqual(result.comparison.mismatched, ["b"])
        self.assertIn("b", result.comparison.details)

    def test_missing_pinned_version_is_a_replay_error(self):
        self.executions.record_run(
            self.workflow_id,
            99,
            make_run("orphan", {"a": {"echo": "v1-a"}}),
        )
        with self.assertRaises(ReplayError):
            asyncio.run(self.service.replay("orphan", self._runner(), role="OWNER"))

    def test_tampered_pinned_version_is_refused(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE workflow_versions SET dsl_text = ? WHERE workflow_id = ? AND version = 1",
                (V2, self.workflow_id),
            )
        with self.assertRaises(ReplayError):
            self._replay()

    def test_viewer_role_may_not_replay(self):
        with self.assertRaises(PermissionError):
            self._replay(role="VIEWER")

    def test_unknown_execution_raises_key_error(self):
        with self.assertRaises(KeyError):
            asyncio.run(self.service.replay("ghost", self._runner(), role="OWNER"))

    def test_replay_is_audited_on_the_source_chain(self):
        result = self._replay()
        events = [e for e in self.audit.events(self.source["id"]) if e["event"] == "run.replay"]
        self.assertEqual(len(events), 1)
        payload = events[0]["payload"]
        self.assertIn(result.replay_execution_id, payload)
        self.assertIn('"match": true', payload)
        self.assertTrue(self.audit.verify(self.source["id"]))

    def test_result_serialises_for_the_api(self):
        body = self._replay().as_dict()
        self.assertEqual(
            set(body),
            {
                "source_execution_id",
                "replay_execution_id",
                "workflow_id",
                "version",
                "status",
                "comparison",
            },
        )
        self.assertTrue(body["comparison"]["match"])


if __name__ == "__main__":
    unittest.main()
