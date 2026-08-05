"""Phase 10 item [99]: `shell` nodes execute for real — inside the sandbox.

The worker used to record the command it *would* run. These tests hold the
replacement to the same standard the sandbox gate is held to:

- the command really runs, and its files land in the run's workspace, which is
  where the durable step evidence points;
- a command that reaches outside the workspace is **denied by the gate before
  dispatch**, so no worker ever sees it;
- a hanging command fails the step at its declared timeout;
- the step output is deterministic (a replay compares summaries, FR-13);
- a workspace the dispatcher never handed out is refused by the worker.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import init_db
from app.models import ExecutionStore, Recorder, WorkflowStore
from bus.backend import MemoryBackend
from bus.events import new_event
from bus.workers.core import RESULTS_STREAM, steps_stream
from bus.workers.dispatcher import resolve_run_workspace, run_workflow
from bus.workers.shell import declared_workspace, make_shell_worker, resolve_workspace
from sandbox.commands import isolation_available
from storage.audit import POLICY_DECISION, AuditStore

#: An empty root keeps a run's workspace throwaway (see the module docstring).
NO_ROOT = {"ASA_WORKSPACE_ROOT": ""}

WORKFLOW = """workflow: shell-demo
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

#: A command the command-guard considers harmless whose *path* leaves the run
#: workspace: only the gate's workspace boundary can catch this one.
ESCAPE_WORKFLOW = """workflow: shell-escape
version: 1
triggers:
  - manual: true
nodes:
  - id: peek
    type: shell
    run: ls /etc
edges: []
"""

TIMEOUT_WORKFLOW = """workflow: shell-timeout
version: 1
triggers:
  - manual: true
nodes:
  - id: hang
    type: shell
    run: sleep 5
    timeout_s: 0.5
edges: []
"""


async def _pump(worker, stop):
    while not stop.is_set():
        stats = await worker.drain_once()
        if stats.processed == 0 and stats.errors == 0:
            await asyncio.sleep(0.01)


def _run(
    dsl_text: str,
    directory: Path,
    *,
    run_id: str,
    workspace_root=None,
    recorder=None,
    audit_store=None,
):
    """Drive one workflow through the dispatcher with a real shell worker."""
    backend = MemoryBackend()
    worker = make_shell_worker(backend, "shell-exec")
    stop = asyncio.Event()

    async def scenario():
        pump = asyncio.create_task(_pump(worker, stop))
        try:
            run = await run_workflow(
                backend,
                "shell-demo",
                workflows_dir=directory,
                run_id=run_id,
                trigger="manual",
                dsl_text=dsl_text,
                recorder=recorder,
                workspace_root=workspace_root,
                audit_store=audit_store,
            )
        finally:
            stop.set()
            await pump
        dispatched = [evt for _, evt in await backend.read_range(steps_stream("shell"))]
        verdicts = [(evt.type, evt.data) for _, evt in await backend.read_range(RESULTS_STREAM)]
        return run, dispatched, verdicts

    return asyncio.run(scenario())


@unittest.skipUnless(isolation_available(), "bwrap with user namespaces is unavailable")
class RealShellRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.runs = self.dir / "runs"

    def test_files_are_really_created_in_the_run_workspace(self):
        run, dispatched, verdicts = _run(
            WORKFLOW, self.dir, run_id="shell-1", workspace_root=self.runs
        )
        self.assertEqual(run.status, "COMPLETED", run.error)
        workspace = self.runs / "shell-1"
        self.assertEqual((workspace / "demo-app/README.md").read_text(), "# demo-app")
        self.assertEqual(
            sorted(evt.data["workspace"] for evt in dispatched),
            [str(workspace), str(workspace)],
            "every node is dispatched with the run's workspace",
        )
        finished = [data for kind, data in verdicts if kind == "step.finished"]
        self.assertEqual(len(finished), 2)
        output = finished[-1]["output"]
        self.assertEqual(output["mode"], "sandboxed (bwrap)")
        self.assertEqual(output["exit_code"], 0)
        self.assertIn("demo-app/README.md", [change["path"] for change in output["files"]])

    def test_the_step_output_is_deterministic_across_runs(self):
        first = _run(WORKFLOW, self.dir, run_id="shell-det-1", workspace_root=self.runs)[2]
        second = _run(WORKFLOW, self.dir, run_id="shell-det-2", workspace_root=self.runs)[2]
        outputs = [
            [data["output"] for kind, data in verdicts if kind == "step.finished"]
            for verdicts in (first, second)
        ]
        self.assertEqual(outputs[0], outputs[1], "a different summary would break replay")

    def test_a_temporary_workspace_is_used_and_removed_without_a_configured_root(self):
        with patch.dict(os.environ, NO_ROOT):
            run, dispatched, _ = _run(WORKFLOW, self.dir, run_id="shell-2")
        self.assertEqual(run.status, "COMPLETED", run.error)
        workspace = Path(dispatched[0].data["workspace"])
        self.assertTrue(workspace.is_absolute())
        self.assertNotEqual(workspace.parent, self.runs)
        self.assertFalse(workspace.exists(), "a throwaway workspace must be cleaned up")

    def test_a_hanging_command_fails_the_step_at_its_timeout(self):
        run, _, verdicts = _run(
            TIMEOUT_WORKFLOW, self.dir, run_id="shell-3", workspace_root=self.runs
        )
        self.assertEqual(run.status, "FAILED")
        self.assertIn("timed out", run.nodes["hang"].error)
        failed = [data for kind, data in verdicts if kind == "step.failed"]
        self.assertIn("timed out", failed[0]["error"])

    def test_the_durable_step_row_carries_the_real_output(self):
        path = self.dir / "app.db"
        init_db(path)
        recorder = Recorder(path)
        run, _, _ = _run(
            WORKFLOW, self.dir, run_id="shell-4", workspace_root=self.runs, recorder=recorder
        )
        self.assertEqual(run.status, "COMPLETED", run.error)

        executions = ExecutionStore(path)
        workflow_id = WorkflowStore(path).ensure_workflow("shell-demo")
        stored = executions.record_run(workflow_id, 1, run, triggered_by="manual")
        steps = executions.list_steps(str(stored["id"]))
        summaries = {step["node_id"]: step["output_summary"] for step in steps}
        self.assertEqual(sorted(summaries), ["readme", "scaffold"])
        self.assertIn("sandboxed", summaries["readme"])
        self.assertIn("demo-app/README.md", summaries["readme"])


class WorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_command_reaching_outside_the_workspace_is_denied_before_dispatch(self):
        path = self.dir / "app.db"
        init_db(path)
        audit = AuditStore(path)
        run, dispatched, _ = _run(
            ESCAPE_WORKFLOW,
            self.dir,
            run_id="shell-5",
            workspace_root=self.dir / "runs",
            audit_store=audit,
        )
        self.assertEqual(run.status, "FAILED")
        node = run.nodes["peek"]
        self.assertEqual(node.status, "FAILED")
        self.assertEqual(node.error, "policy DENY")
        self.assertEqual(dispatched, [], "a denied command must never be dispatched")
        self.assertEqual(
            list((self.dir / "runs" / "shell-5").rglob("*")),
            [],
            "nothing ran, so nothing was written",
        )
        decision = [
            row
            for row in audit.events("shell-5")
            if row["event"] == POLICY_DECISION and '"node_id": "peek"' in row["payload"]
        ]
        self.assertTrue(decision, "the denial reason must be on the audit chain")
        self.assertIn("outside the workspace", decision[0]["payload"])
        self.assertTrue(audit.verify_chain("shell-5").ok)

    def test_without_os_isolation_the_step_fails_closed(self):
        """No bwrap means no command run — the step fails with the sandbox reason."""

        async def scenario():
            backend = MemoryBackend()
            worker = make_shell_worker(backend, "no-isolation")
            workspace = self.dir / "runs" / "shell-6"
            await backend.publish(
                steps_stream("shell"),
                new_event(
                    "step.dispatched",
                    {
                        "node_id": "a",
                        "node": {"type": "shell", "params": {"run": "touch escaped.txt"}},
                        "workspace": str(workspace),
                    },
                    correlation_id="shell-6",
                    idempotency_key="shell-6:a",
                ),
            )
            with patch("sandbox.runner.bwrap_available", return_value=False):
                stats = await worker.drain_once()
            self.assertEqual(stats.processed, 1)
            return [(evt.type, evt.data) for _, evt in await backend.read_range(RESULTS_STREAM)]

        verdicts = asyncio.run(scenario())
        self.assertEqual(verdicts[-1][0], "step.failed")
        self.assertIn("OS isolation", verdicts[-1][1]["error"])
        self.assertEqual(
            list((self.dir / "runs").rglob("escaped.txt")), [], "nothing may run unguarded"
        )

    def test_declared_workspace_must_be_absolute_and_inside_an_allowed_root(self):
        with self.assertRaises(RuntimeError):
            declared_workspace("relative/path")
        with self.assertRaises(RuntimeError):
            declared_workspace("/etc")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(declared_workspace(tmp), Path(tmp).resolve())
        self.assertIsNone(declared_workspace(""))

    def test_worker_falls_back_to_its_own_temporary_workspace(self):
        workspace, mode, temporary = resolve_workspace({}, {})
        self.assertEqual(mode, "temporary")
        self.assertTrue(temporary)
        self.assertTrue(workspace.is_dir())
        self.assertIsNotNone(declared_workspace(str(workspace)))

    def test_an_in_process_caller_may_hand_over_its_own_workspace(self):
        workspace = self.dir / "explicit"
        workspace.mkdir()
        resolved, mode, temporary = resolve_workspace({}, {"workspace": workspace})
        self.assertEqual(resolved, workspace)
        self.assertEqual(mode, "run")
        self.assertFalse(temporary)

    def test_relative_run_root_is_rejected_instead_of_written_to(self):
        with self.assertRaises(RuntimeError):
            declared_workspace("data/workspaces/run-1")

    def test_resolve_run_workspace_under_a_configured_root(self):
        workspace, temporary = resolve_run_workspace("rid-9", self.dir / "runs")
        self.assertFalse(temporary)
        self.assertEqual(workspace, self.dir / "runs" / "rid-9")
        self.assertTrue(workspace.is_dir())

    def test_resolve_run_workspace_without_a_root_is_a_throwaway(self):
        with patch.dict(os.environ, NO_ROOT):
            workspace, temporary = resolve_run_workspace("rid-10")
        self.assertTrue(temporary)
        self.assertTrue(workspace.is_dir())
        self.assertNotIn(str(Path.cwd()), str(workspace))


if __name__ == "__main__":
    unittest.main()
