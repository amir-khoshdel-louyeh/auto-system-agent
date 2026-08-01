"""Phase 5 item [54]: replay acceptance evidence (W2).

The scenario walks the W2 replay bullet end to end: run a workflow over the
bus, pin its version, change the (mutable) YAML file on disk, then replay —
the rerun must use the pinned version, produce comparable normalized outputs,
be audited on the source chain, and report a mismatch when the rerun's
behavior actually differs.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from app.models import ExecutionStore, WorkflowStore
from app.replay import ReplayService
from bus.backend import MemoryBackend
from bus.workers.dispatcher import run_workflow
from bus.workers.shell import make_shell_worker
from engine.dsl.loader import load_text
from storage.audit import AuditStore

V1 = """workflow: replay-demo
version: 1
triggers:
  - manual: true
nodes:
  - id: scaffold
    type: shell
    run: mkdir -p demo-app
  - id: readme
    type: shell
    run: printf '# demo-app' > demo-app/README.md
edges: [[scaffold, readme]]
"""

V2 = """workflow: replay-demo
version: 2
triggers:
  - manual: true
nodes:
  - id: scaffold
    type: shell
    run: mkdir -p other-app
edges: []
"""


async def _pump(worker, stop):
    while not stop.is_set():
        stats = await worker.drain_once()
        if stats.processed == 0 and stats.errors == 0:
            await asyncio.sleep(0.01)


class ReplayAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.file = self.dir / "replay-demo.yaml"
        self.file.write_text(V1, encoding="utf-8")
        self.db = init_db(self.dir / "app.db")
        self.workflows = WorkflowStore(self.db)
        self.executions = ExecutionStore(self.db)
        self.audit = AuditStore(self.db)
        self.service = ReplayService(self.workflows, self.executions, self.audit)

    def _pinned_runner(self, exec_fn=None):
        """Run the DSL text handed to it over the bus (pinned-version path)."""

        async def runner(dsl_text: str, replay_id: str):
            backend = MemoryBackend()
            worker = make_shell_worker(backend, "replay", exec_fn=exec_fn)
            stop = asyncio.Event()
            pump = asyncio.create_task(_pump(worker, stop))
            try:
                return await run_workflow(
                    backend,
                    "replay-demo",
                    workflows_dir=self.dir,
                    run_id=replay_id,
                    trigger="replay",
                    dsl_text=dsl_text,
                )
            finally:
                stop.set()
                await pump

        return runner

    def _source_run(self):
        """Run the workflow over the bus and persist it as an immutable pin."""

        async def scenario():
            backend = MemoryBackend()
            worker = make_shell_worker(backend, "source")
            stop = asyncio.Event()
            pump = asyncio.create_task(_pump(worker, stop))
            try:
                return await run_workflow(
                    backend,
                    "replay-demo",
                    workflows_dir=self.dir,
                    run_id="w2-source",
                    trigger="file.arrived",
                )
            finally:
                stop.set()
                await pump

        run = asyncio.run(scenario())
        self.assertEqual(run.status, "COMPLETED")
        workflow = load_text(V1)
        workflow_id = self.workflows.ensure_workflow(workflow.name)
        self.workflows.publish_version(workflow_id, workflow.version, V1)
        self.executions.record_run(workflow_id, workflow.version, run, triggered_by="file.arrived")
        return workflow_id

    def test_w2_replay_uses_the_pinned_version_after_the_file_changes(self):
        self._source_run()
        self.file.write_text(V2, encoding="utf-8")  # mutable file moves on

        result = asyncio.run(
            self.service.replay("w2-source", self._pinned_runner(), role="OPERATOR")
        )

        self.assertEqual(result.version, 1)
        self.assertEqual(result.status, "COMPLETED")
        self.assertTrue(result.comparison.match)
        self.assertEqual(result.comparison.compared, 2)
        stored = self.executions.get(result.replay_execution_id)
        self.assertEqual(stored["version"], 1)
        self.assertEqual(stored["triggered_by"], "replay")
        # The pinned text, not the new file, was executed.
        nodes = {step["node_id"] for step in stored["steps"]}
        self.assertEqual(nodes, {"scaffold", "readme"})

    def test_w2_replay_reports_a_mismatch_when_behavior_differs(self):
        self._source_run()

        async def different(node_id, node, ctx):
            return {"ran": "something else", "node": node_id}

        result = asyncio.run(
            self.service.replay("w2-source", self._pinned_runner(exec_fn=different), role="OWNER")
        )
        self.assertFalse(result.comparison.match)
        self.assertEqual(sorted(result.comparison.mismatched), ["readme", "scaffold"])
        self.assertEqual(result.comparison.missing, [])

    def test_w2_replay_is_recorded_on_the_source_audit_chain(self):
        self._source_run()
        result = asyncio.run(self.service.replay("w2-source", self._pinned_runner(), role="OWNER"))
        payload = self.audit.events("w2-source")[-1]
        self.assertEqual(payload["event"], "run.replay")
        self.assertIn(result.replay_execution_id, payload["payload"])
        self.assertTrue(self.audit.verify("w2-source"))

    def test_source_run_is_not_modified_by_replay(self):
        self._source_run()
        before = self.executions.get("w2-source")
        asyncio.run(self.service.replay("w2-source", self._pinned_runner(), role="OWNER"))
        after = self.executions.get("w2-source")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
