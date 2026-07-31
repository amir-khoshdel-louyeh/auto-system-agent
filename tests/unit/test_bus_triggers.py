"""Phase 4 item [44]: manual, file-arrival and schedule triggers (FR-04).

All three trigger kinds run the same `run_workflow` path over the bus: the
run completes, and the `plan.created` event records which trigger started it.
Bus triggers build their frozen contract event; manual runs directly.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from bus.backend import MemoryBackend
from bus.triggers import (
    FILE_ARRIVED,
    MANUAL,
    SCHEDULE_TICK,
    TRIGGER_KINDS,
    build_trigger_event,
    trigger_file_arrived,
    trigger_manual,
    trigger_schedule_tick,
)
from bus.workers.dispatcher import PLANS_STREAM
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


async def _exec_fn(node_id, node, ctx):
    return {"ok": node_id}


async def _pump(worker, stop):
    while not stop.is_set():
        moved = await worker.drain_once()
        if moved.processed == 0:
            await asyncio.sleep(0.01)


class TriggerEventTests(unittest.TestCase):
    def test_three_supported_trigger_kinds(self):
        self.assertEqual(TRIGGER_KINDS, (MANUAL, FILE_ARRIVED, SCHEDULE_TICK))

    def test_bus_trigger_event_types(self):
        for kind in (FILE_ARRIVED, SCHEDULE_TICK):
            with self.subTest(kind=kind):
                event = build_trigger_event(kind, "simple")
                self.assertEqual(event.type, kind)
                self.assertEqual(event.data["workflow"], "simple")

    def test_manual_is_not_a_bus_event(self):
        with self.assertRaises(ValueError):
            build_trigger_event(MANUAL, "simple")


class TriggerRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        (self.dir / "simple.yaml").write_text(SIMPLE, encoding="utf-8")

    def _run_trigger(self, trigger_fn):
        async def scenario():
            backend = MemoryBackend()
            worker = make_shell_worker(backend, "dispatcher", exec_fn=_exec_fn)
            stop = asyncio.Event()
            pump = asyncio.create_task(_pump(worker, stop))
            try:
                result = await trigger_fn(backend)
            finally:
                stop.set()
                await pump
            plans = [evt for _, evt in await backend.read_range(PLANS_STREAM)]
            return result, plans

        return asyncio.run(scenario())

    def test_manual_trigger_runs(self):
        result, plans = self._run_trigger(
            lambda backend: trigger_manual(backend, "simple", workflows_dir=self.dir)
        )
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(plans[0].data["trigger"], MANUAL)

    def test_file_arrived_trigger_runs(self):
        result, plans = self._run_trigger(
            lambda backend: trigger_file_arrived(backend, "simple", workflows_dir=self.dir)
        )
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(plans[0].data["trigger"], FILE_ARRIVED)

    def test_schedule_tick_trigger_runs(self):
        result, plans = self._run_trigger(
            lambda backend: trigger_schedule_tick(backend, "simple", workflows_dir=self.dir)
        )
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(plans[0].data["trigger"], SCHEDULE_TICK)

    def test_all_nodes_executed(self):
        result, _ = self._run_trigger(
            lambda backend: trigger_manual(backend, "simple", workflows_dir=self.dir)
        )
        self.assertEqual(result.nodes["a"].status, "OK")
        self.assertEqual(result.nodes["b"].status, "OK")


if __name__ == "__main__":
    unittest.main()
