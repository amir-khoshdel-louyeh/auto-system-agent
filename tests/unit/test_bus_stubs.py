"""Phase 4 item [41]: GUI contract workers are explicit stubs (FR-04).

`gui_click`/`gui_type` need OS-tree/VLM grounding. Until then their workers
are deliberately non-production: they consume their tasks and answer
`step.failed` with a precise reason (fail closed, never a silent drop or a
fake success). `custom` left the stub set in Phase 8: it now runs registered
plugins in the sandbox (`bus.workers.custom`).
"""

import asyncio
import unittest

from bus.backend import MemoryBackend
from bus.events import new_event
from bus.workers.core import RESULTS_STREAM, steps_stream
from bus.workers.stubs import STUB_REASONS, make_stub_worker


def _run(coro):
    return asyncio.run(coro)


class StubWorkerTests(unittest.TestCase):
    def _execute(self, node_type: str):
        async def scenario():
            backend = MemoryBackend()
            worker = make_stub_worker(backend, "g", node_type)
            await backend.publish(
                steps_stream(node_type),
                new_event(
                    "step.dispatched",
                    {"node_id": "n", "node": {"type": node_type, "params": {}}},
                    correlation_id="r1",
                    idempotency_key=f"r1:n:{node_type}",
                ),
            )
            await worker.drain_once()
            results = await backend.read_range(RESULTS_STREAM)
            return results[-1][1]

        return _run(scenario())

    def test_every_future_worker_type_has_a_stub(self):
        self.assertEqual(set(STUB_REASONS), {"gui_click", "gui_type"})

    def test_custom_is_no_longer_a_stub(self):
        self.assertNotIn("custom", STUB_REASONS)

    def test_stubs_fail_closed_with_an_explicit_reason(self):
        for node_type in ("gui_click", "gui_type"):
            with self.subTest(node_type=node_type):
                event = self._execute(node_type)
                self.assertEqual(event.type, "step.failed")
                self.assertIn("stub", event.data["error"])
                self.assertIn("phase", event.data["error"])

    def test_stub_never_reports_success(self):
        for node_type in ("gui_click", "gui_type"):
            with self.subTest(node_type=node_type):
                event = self._execute(node_type)
                self.assertNotEqual(event.type, "step.finished")


if __name__ == "__main__":
    unittest.main()
