"""Phase 4 item [43]: persist step state before acknowledging events (FR-11).

The worker writes durable step state before it publishes the verdict, and the
verdict is published before the delivery is acknowledged. If persistence
fails the handler raises, so the delivery is retried / dead-lettered rather
than acked with no durable record.
"""

import asyncio
import unittest

from bus.backend import MemoryBackend
from bus.events import new_event
from bus.workers.core import RESULTS_STREAM, make_step_worker, steps_stream


def _run(coro):
    return asyncio.run(coro)


class RecordingBackend(MemoryBackend):
    """MemoryBackend that records the order of publishes."""

    def __init__(self):
        super().__init__()
        self.trace: list[tuple[str, str]] = []

    async def publish(self, stream, event):
        self.trace.append(("publish", stream))
        return await super().publish(stream, event)


class PersistOrderingTests(unittest.TestCase):
    def test_step_state_is_written_before_the_verdict(self):
        async def scenario():
            backend = RecordingBackend()

            def persist(run_id, node_id, state, error, output):
                backend.trace.append(("persist", state))

            async def exec_fn(node_id, node, ctx):
                return {"ok": node_id}

            worker = make_step_worker(backend, "g", "shell", exec_fn, persist=persist)
            await backend.publish(
                steps_stream("shell"),
                new_event(
                    "step.dispatched",
                    {"node_id": "a", "node": {"type": "shell", "params": {}}},
                    correlation_id="r1",
                    idempotency_key="r1:a",
                ),
            )
            await worker.drain_once()

            # persist lands before the verdict is published
            self.assertEqual(
                backend.trace,
                [
                    ("publish", steps_stream("shell")),
                    ("persist", "SUCCEEDED"),
                    ("publish", RESULTS_STREAM),
                ],
            )

        _run(scenario())

    def test_failed_node_persists_failed_state(self):
        async def scenario():
            backend = MemoryBackend()
            rows: list[tuple[str, str, str]] = []

            def persist(run_id, node_id, state, error, output):
                rows.append((node_id, state, error))

            async def exec_fn(node_id, node, ctx):
                raise RuntimeError("boom")

            worker = make_step_worker(backend, "g", "shell", exec_fn, persist=persist)
            await backend.publish(
                steps_stream("shell"),
                new_event(
                    "step.dispatched",
                    {"node_id": "a", "node": {"type": "shell", "params": {}}},
                    correlation_id="r1",
                    idempotency_key="r1:a",
                ),
            )
            await worker.drain_once()

            self.assertEqual(rows, [("a", "FAILED", "boom")])

        _run(scenario())

    def test_persistence_failure_is_not_acked_silently(self):
        async def scenario():
            backend = MemoryBackend()

            def persist(run_id, node_id, state, error, output):
                raise RuntimeError("disk full")

            async def exec_fn(node_id, node, ctx):
                return {}

            worker = make_step_worker(
                backend, "g", "shell", exec_fn, persist=persist, max_retries=0
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
            self.assertTrue(parked)
            self.assertIn("disk full", parked[0][2])

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
