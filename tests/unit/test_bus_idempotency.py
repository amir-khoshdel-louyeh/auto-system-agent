"""Phase 4 item [39]: correlation and idempotency key handling (FR-09).

Every event carries a correlation id (the run) and an idempotency key.
Publishing the same key twice appends once, and a consumer that sees a
redelivered key processes it exactly once while still acknowledging it.
"""

import asyncio
import unittest

from bus.backend import MemoryBackend
from bus.consumer import Runner, RunStats
from bus.events import new_event


def _run(coro):
    return asyncio.run(coro)


class PublishIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryBackend()

    def test_same_key_publishes_once(self):
        async def scenario():
            event = new_event("plan.created", {"n": 1}, idempotency_key="k")
            first = await self.backend.publish("s", event)
            second = await self.backend.publish("s", event)
            self.assertEqual(first, second)
            self.assertEqual(len(await self.backend.read_range("s")), 1)

        _run(scenario())

    def test_same_key_on_different_streams_is_independent(self):
        async def scenario():
            event = new_event("plan.created", idempotency_key="k")
            await self.backend.publish("s1", event)
            await self.backend.publish("s2", event)
            self.assertEqual(len(await self.backend.read_range("s1")), 1)
            self.assertEqual(len(await self.backend.read_range("s2")), 1)

        _run(scenario())

    def test_correlation_id_round_trips(self):
        async def scenario():
            await self.backend.publish(
                "s", new_event("step.dispatched", {"node_id": "a"}, correlation_id="run-1")
            )
            batch = await self.backend.read_group("s", "g", "c")
            self.assertEqual(batch[0][1].correlation_id, "run-1")

        _run(scenario())


class DuplicateDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryBackend()

    def test_redelivered_key_is_processed_once_but_acknowledged(self):
        async def scenario():
            calls: list[str] = []
            runner = Runner(self.backend, "g", ["s"], max_retries=0)

            @runner.on("plan.created")
            async def handle(event):
                calls.append(event.id)

            event = new_event("plan.created", idempotency_key="dup")
            await self.backend.publish("s", event)
            batch = await self.backend.read_group("s", "g", "c")
            message_id, delivered = batch[0]

            first = RunStats()
            await runner._process("s", message_id, delivered, first)
            second = RunStats()
            await runner._process("s", message_id, delivered, second)

            self.assertEqual(calls, [event.id], "handler must run once per key")
            self.assertEqual(first.processed, 1)
            self.assertEqual(second.duplicates, 1)
            # the duplicate is acknowledged, not left pending
            self.assertEqual(await self.backend.pending_count("s", "g"), 0)

        _run(scenario())

    def test_distinct_keys_both_process(self):
        async def scenario():
            calls: list[str] = []
            runner = Runner(self.backend, "g", ["s"], max_retries=0)

            @runner.on("plan.created")
            async def handle(event):
                calls.append(event.id)

            await self.backend.publish("s", new_event("plan.created", idempotency_key="a"))
            await self.backend.publish("s", new_event("plan.created", idempotency_key="b"))
            stats = await runner.drain_once()
            self.assertEqual(stats.processed, 2)
            self.assertEqual(len(calls), 2)

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
