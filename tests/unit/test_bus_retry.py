"""Phase 4 item [42]: retry backoff and dead-letter stream (FR-09, §10.5).

A failing handler is retried with exponential backoff up to the limit; once
exhausted the event is parked on the stream's DLQ with a reason and the
delivery is acknowledged (so it is not redelivered forever).
"""

import asyncio
import unittest

from bus.backend import MemoryBackend
from bus.consumer import Runner
from bus.events import new_event


def _run(coro):
    return asyncio.run(coro)


class BackoffTests(unittest.TestCase):
    def test_backoff_grows_exponentially(self):
        runner = Runner(MemoryBackend(), "g", ["s"], base_delay_ms=100, max_delay_ms=5000)
        self.assertEqual(runner._backoff_s(1), 0.1)
        self.assertEqual(runner._backoff_s(2), 0.2)
        self.assertEqual(runner._backoff_s(3), 0.4)

    def test_backoff_is_capped(self):
        runner = Runner(MemoryBackend(), "g", ["s"], base_delay_ms=1000, max_delay_ms=3000)
        self.assertEqual(runner._backoff_s(10), 3.0)


class DeadLetterTests(unittest.TestCase):
    def _consume_with(self, handler, *, max_retries: int):
        async def scenario():
            backend = MemoryBackend()
            runner = Runner(backend, "g", ["s"], max_retries=max_retries, base_delay_ms=1)
            runner.on("plan.created", handler)
            await backend.publish("s", new_event("plan.created", {"n": 1}))
            stats = await runner.drain_once()
            return backend, stats

        return _run(scenario())

    def test_exhausted_handler_reaches_the_dlq(self):
        attempts = {"n": 0}

        async def always_fail(event):
            attempts["n"] += 1
            raise RuntimeError("boom")

        backend, stats = self._consume_with(always_fail, max_retries=2)
        self.assertEqual(attempts["n"], 3)  # 1 try + 2 retries
        self.assertEqual(stats.dlq, 1)
        self.assertEqual(stats.errors, 3)

        parked = _run(backend.dlq_list("s"))
        self.assertEqual(len(parked), 1)
        _, event, reason = parked[0]
        self.assertEqual(event.type, "plan.created")
        self.assertIn("boom", reason)
        self.assertIn("3 attempts", reason)

    def test_dlq_delivery_is_acknowledged(self):
        async def always_fail(event):
            raise RuntimeError("nope")

        backend, _ = self._consume_with(always_fail, max_retries=1)
        self.assertEqual(_run(backend.pending_count("s", "g")), 0)

    def test_transient_failure_recovers_without_dlq(self):
        calls = {"n": 0}

        async def flaky(event):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")

        backend, stats = self._consume_with(flaky, max_retries=3)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(stats.processed, 1)
        self.assertEqual(stats.dlq, 0)
        self.assertEqual(_run(backend.dlq_list("s")), [])


if __name__ == "__main__":
    unittest.main()
