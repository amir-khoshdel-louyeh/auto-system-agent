"""Phase 4 item [45]: duplicate-delivery, ordering and DLQ tests (FR-09).

Integration-level evidence over the ephemeral streams: a duplicate dispatch
executes its side effect once, events are processed in stream order, an
exhausted delivery reaches the DLQ, and triggering the same run twice does
not duplicate side effects.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from bus.backend import MemoryBackend
from bus.consumer import Runner
from bus.events import new_event
from bus.triggers import trigger_file_arrived
from bus.workers.core import RESULTS_STREAM, make_step_worker, steps_stream
from bus.workers.shell import make_shell_worker

SIMPLE = """workflow: simple
version: 1
triggers:
  - manual: true
nodes:
  - id: a
    type: shell
    run: echo hi
  - id: b
    type: shell
    run: echo bye
edges: [[a, b]]
"""


async def _pump(worker, stop):
    while not stop.is_set():
        moved = await worker.drain_once()
        if moved.processed == 0:
            await asyncio.sleep(0.01)


class DuplicateDeliveryTests(unittest.TestCase):
    def test_duplicate_dispatch_executes_once(self):
        async def scenario():
            backend = MemoryBackend()
            calls: list[str] = []

            async def exec_fn(node_id, node, ctx):
                calls.append(node_id)
                return {"ok": node_id}

            worker = make_shell_worker(backend, "g", exec_fn=exec_fn)
            event = new_event(
                "step.dispatched",
                {"node_id": "a", "node": {"type": "shell", "params": {}}},
                correlation_id="r1",
                idempotency_key="r1:a",
            )
            await backend.publish(steps_stream("shell"), event)
            await backend.publish(steps_stream("shell"), event)  # duplicate
            await worker.drain_once()

            self.assertEqual(calls, ["a"], "side effect must run exactly once")
            finished = [
                evt
                for _, evt in await backend.read_range(RESULTS_STREAM)
                if evt.type == "step.finished"
            ]
            self.assertEqual(len(finished), 1)

        asyncio.run(scenario())

    def test_same_run_triggered_twice_has_no_duplicate_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "simple.yaml").write_text(SIMPLE, encoding="utf-8")

            async def scenario():
                backend = MemoryBackend()
                calls: list[str] = []

                async def exec_fn(node_id, node, ctx):
                    calls.append(node_id)
                    return {"ok": node_id}

                worker = make_shell_worker(backend, "d", exec_fn=exec_fn)
                stop = asyncio.Event()
                pump = asyncio.create_task(_pump(worker, stop))
                try:
                    first = await trigger_file_arrived(
                        backend, "simple", workflows_dir=directory, run_id="run-x"
                    )
                    second = await trigger_file_arrived(
                        backend, "simple", workflows_dir=directory, run_id="run-x"
                    )
                finally:
                    stop.set()
                    await pump

                self.assertEqual(first.status, "COMPLETED")
                self.assertEqual(second.status, "COMPLETED")
                # two nodes, once each: the second trigger reuses the verdicts
                self.assertEqual(sorted(calls), ["a", "b"])
                plans = [
                    evt
                    for _, evt in await backend.read_range("plans")
                    if evt.type == "plan.created"
                ]
                self.assertEqual(len(plans), 1)

            asyncio.run(scenario())


class OrderingTests(unittest.TestCase):
    def test_events_are_processed_in_stream_order(self):
        async def scenario():
            backend = MemoryBackend()
            seen: list[int] = []
            runner = Runner(backend, "g", ["s"], max_retries=0)

            @runner.on("plan.created")
            async def handle(event):
                seen.append(event.data["n"])

            for n in range(10):
                await backend.publish("s", new_event("plan.created", {"n": n}))
            await runner.drain_once()
            self.assertEqual(seen, list(range(10)))

        asyncio.run(scenario())


class FailureDomainTests(unittest.TestCase):
    """A node failure is a verdict; a delivery failure reaches the DLQ."""

    def test_node_failure_is_a_verdict_not_a_dlq(self):
        async def scenario():
            backend = MemoryBackend()

            async def exec_fn(node_id, node, ctx):
                raise RuntimeError("boom")

            worker = make_step_worker(backend, "g", "shell", exec_fn, max_retries=1)
            await backend.publish(
                steps_stream("shell"),
                new_event(
                    "step.dispatched",
                    {"node_id": "a", "node": {"type": "shell", "params": {}}},
                    correlation_id="r1",
                    idempotency_key="r1:a",
                ),
            )
            stats = await worker.drain_once()
            self.assertEqual(stats.dlq, 0)
            self.assertEqual((await backend.read_range(RESULTS_STREAM))[-1][1].type, "step.failed")

        asyncio.run(scenario())

    def test_delivery_failure_reaches_the_dlq(self):
        async def scenario():
            backend = MemoryBackend()

            def persist(run_id, node_id, state, error, output):
                raise RuntimeError("disk full")

            async def exec_fn(node_id, node, ctx):
                return {"ok": node_id}

            worker = make_step_worker(
                backend, "g", "shell", exec_fn, persist=persist, max_retries=1
            )
            await backend.publish(
                steps_stream("shell"),
                new_event(
                    "step.dispatched",
                    {"node_id": "a", "node": {"type": "shell", "params": {}}},
                    correlation_id="r1",
                    idempotency_key="r1:a",
                ),
            )
            stats = await worker.drain_once()
            self.assertEqual(stats.dlq, 1)
            parked = await backend.dlq_list(steps_stream("shell"))
            self.assertIn("disk full", parked[0][2])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
