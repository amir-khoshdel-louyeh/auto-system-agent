"""Phase 4 item [38]: Redis Streams producer and consumer group (FR-09).

The broker contract (publish / ensure_group / read_group / acknowledge /
pending / range read / DLQ) is exercised against the ephemeral in-memory
Streams clone; the same cases run against a real Redis when one is reachable
(skipped otherwise so CI without a broker stays green).
"""

import asyncio
import unittest
import uuid

from bus.backend import Backend, MemoryBackend
from bus.events import new_event
from bus.redis_backend import RedisBackend

REDIS_URL = "redis://localhost:6379/0"


def _redis_reachable() -> bool:
    try:
        import redis.asyncio as redis
    except ImportError:  # pragma: no cover - redis extra not installed
        return False

    async def ping() -> bool:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        try:
            await client.ping()
            return True
        finally:
            await client.aclose()

    try:
        return asyncio.run(ping())
    except Exception:  # noqa: BLE001 - any failure means "no broker here"
        return False


REDIS_AVAILABLE = _redis_reachable()


def _run(coro):
    return asyncio.run(coro)


class MemoryBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryBackend()

    def test_publish_then_consume_and_ack(self):
        async def scenario():
            message_id = await self.backend.publish("s", new_event("plan.created", {"n": 1}))
            batch = await self.backend.read_group("s", "g", "c1")
            self.assertEqual([mid for mid, _ in batch], [message_id])
            self.assertEqual(await self.backend.pending_count("s", "g"), 1)
            await self.backend.acknowledge("s", "g", message_id)
            self.assertEqual(await self.backend.pending_count("s", "g"), 0)

        _run(scenario())

    def test_messages_are_ordered(self):
        async def scenario():
            for n in range(5):
                await self.backend.publish("s", new_event("plan.created", {"n": n}))
            batch = await self.backend.read_group("s", "g", "c1", count=10)
            self.assertEqual([e.data["n"] for _, e in batch], [0, 1, 2, 3, 4])

        _run(scenario())

    def test_consumer_groups_are_independent(self):
        async def scenario():
            await self.backend.publish("s", new_event("plan.created"))
            first = await self.backend.read_group("s", "g1", "c1")
            second = await self.backend.read_group("s", "g2", "c1")
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)

        _run(scenario())

    def test_ack_does_not_remove_history_for_replay(self):
        async def scenario():
            message_id = await self.backend.publish("s", new_event("plan.created", {"n": 1}))
            await self.backend.read_group("s", "g", "c1")
            await self.backend.acknowledge("s", "g", message_id)
            history = await self.backend.read_range("s")
            self.assertEqual([e.data["n"] for _, e in history], [1])

        _run(scenario())

    def test_dead_letter_listing(self):
        async def scenario():
            event = new_event("step.failed", {"node_id": "a"})
            await self.backend.to_dlq("s", event, "boom")
            parked = await self.backend.dlq_list("s")
            self.assertEqual(len(parked), 1)
            self.assertEqual(parked[0][2], "boom")

        _run(scenario())


class BackendContractTests(unittest.TestCase):
    def test_both_backends_implement_the_abc(self):
        self.assertIsInstance(MemoryBackend(), Backend)
        self.assertIsInstance(RedisBackend(REDIS_URL), Backend)


@unittest.skipUnless(REDIS_AVAILABLE, f"no Redis broker at {REDIS_URL}")
class RedisBackendTests(unittest.TestCase):  # pragma: no cover - needs a broker
    def setUp(self):
        self.backend = RedisBackend(REDIS_URL)
        self.stream = f"test:bus:{uuid.uuid4().hex}"

    def tearDown(self):
        async def cleanup():
            client = await self.backend._get_client()
            await client.delete(self.stream, f"{self.stream}:dlq")
            await self.backend.close()

        _run(cleanup())

    def test_publish_consume_ack_round_trip(self):
        async def scenario():
            message_id = await self.backend.publish(
                self.stream, new_event("plan.created", {"n": 1}, idempotency_key="k1")
            )
            batch = await self.backend.read_group(self.stream, "g", "c1")
            self.assertEqual([mid for mid, _ in batch], [message_id])
            self.assertEqual(await self.backend.pending_count(self.stream, "g"), 1)
            await self.backend.acknowledge(self.stream, "g", message_id)
            self.assertEqual(await self.backend.pending_count(self.stream, "g"), 0)

        _run(scenario())

    def test_idempotent_publish_returns_same_id(self):
        async def scenario():
            event = new_event("plan.created", {"n": 1}, idempotency_key="dupe")
            first = await self.backend.publish(self.stream, event)
            second = await self.backend.publish(self.stream, event)
            self.assertEqual(first, second)

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
