"""Phase 4 item [40]: shell and approval workers (FR-09, FR-10).

One worker consumes `steps:<type>` tasks and answers on `steps:results`.
The shell worker re-runs the policy gate (defense in depth) so a dangerous
command never reaches execution, and the approval worker decides quorum==1
while parking anything higher for the human quorum.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from bus.backend import MemoryBackend
from bus.events import new_event
from bus.workers.approval import make_approval_worker
from bus.workers.core import RESULTS_STREAM, steps_stream
from bus.workers.shell import make_shell_worker
from sandbox.commands import isolation_available


def _run(coro):
    return asyncio.run(coro)


async def _dispatch(backend, node_type, node_id, node, run_id="run-1", workspace=""):
    await backend.publish(
        steps_stream(node_type),
        new_event(
            "step.dispatched",
            {"node_id": node_id, "node": node, "workspace": workspace},
            correlation_id=run_id,
            idempotency_key=f"{run_id}:{node_id}",
        ),
    )


async def _results(backend):
    return [(mid, evt) for mid, evt in await backend.read_range(RESULTS_STREAM)]


class ShellWorkerTests(unittest.TestCase):
    @unittest.skipUnless(isolation_available(), "bwrap with user namespaces is unavailable")
    def test_safe_node_finishes(self):
        """A safe node really runs (item [99]) in the workspace it is handed."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "run"

            async def scenario():
                backend = MemoryBackend()
                worker = make_shell_worker(backend, "g")
                await _dispatch(
                    backend,
                    "shell",
                    "a",
                    {"type": "shell", "params": {"run": "echo hi > hello.txt"}},
                    workspace=str(workspace),
                )
                stats = await worker.drain_once()
                self.assertEqual(stats.processed, 1)
                results = await _results(backend)
                self.assertEqual(results[-1][1].type, "step.finished")
                self.assertEqual(results[-1][1].data["node_id"], "a")
                self.assertEqual(results[-1][1].data["output"]["mode"], "sandboxed (bwrap)")

            _run(scenario())
            self.assertEqual((workspace / "hello.txt").read_text().strip(), "hi")

    def test_dangerous_node_fails_closed_without_running(self):
        async def scenario():
            backend = MemoryBackend()
            executed = {"ran": False}

            async def exec_fn(node_id, node, ctx):
                executed["ran"] = True
                return {}

            worker = make_shell_worker(backend, "g", exec_fn=exec_fn)
            await _dispatch(
                backend, "shell", "wipe", {"type": "shell", "params": {"run": "rm -rf /"}}
            )
            await worker.drain_once()
            results = await _results(backend)
            self.assertEqual(results[-1][1].type, "step.failed")
            self.assertIn("DENY", results[-1][1].data["error"])
            self.assertFalse(executed["ran"], "denied command must never execute")

        _run(scenario())

    def test_injected_exec_fn_is_used(self):
        async def scenario():
            backend = MemoryBackend()

            async def exec_fn(node_id, node, ctx):
                return {"custom": node_id}

            worker = make_shell_worker(backend, "g", exec_fn=exec_fn)
            await _dispatch(backend, "shell", "a", {"type": "shell", "params": {"run": "echo hi"}})
            await worker.drain_once()
            results = await _results(backend)
            self.assertEqual(results[-1][1].data["output"], {"custom": "a"})

        _run(scenario())

    def test_worker_ignores_other_node_types(self):
        async def scenario():
            backend = MemoryBackend()
            worker = make_shell_worker(backend, "g")
            await _dispatch(backend, "shell", "g", {"type": "gui_click", "params": {}})
            await worker.drain_once()
            self.assertEqual(await _results(backend), [])

        _run(scenario())


class ApprovalWorkerTests(unittest.TestCase):
    def test_quorum_one_auto_approves(self):
        async def scenario():
            backend = MemoryBackend()
            worker = make_approval_worker(backend, "g")
            await _dispatch(
                backend, "approval", "gate", {"type": "approval", "params": {"quorum": 1}}
            )
            await worker.drain_once()
            results = await _results(backend)
            self.assertEqual(results[-1][1].type, "step.finished")
            self.assertEqual(results[-1][1].data["output"]["decision"], "approved")

        _run(scenario())

    def test_higher_quorum_parks_for_humans(self):
        async def scenario():
            backend = MemoryBackend()
            worker = make_approval_worker(backend, "g")
            await _dispatch(
                backend, "approval", "gate", {"type": "approval", "params": {"quorum": 2}}
            )
            await worker.drain_once()
            results = await _results(backend)
            self.assertEqual(results[-1][1].type, "step.failed")
            self.assertIn("quorum", results[-1][1].data["error"])

        _run(scenario())

    def test_approval_request_event_is_published(self):
        async def scenario():
            backend = MemoryBackend()
            worker = make_approval_worker(backend, "g")
            await _dispatch(
                backend, "approval", "gate", {"type": "approval", "params": {"quorum": 1}}
            )
            await worker.drain_once()
            events = [evt.type for _, evt in await backend.read_range("approvals")]
            self.assertIn("approval.requested", events)

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
