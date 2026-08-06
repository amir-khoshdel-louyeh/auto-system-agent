"""Post-RC item [102]: chaos and recovery — the failure modes, exercised.

The recovery claim used to rest on retry/DLQ unit tests and one kill-mid-run
test. This suite adds the cases an operator actually meets:

1. the broker is unreachable for a few polls, then comes back — the worker
   keeps running and processes what arrives afterwards instead of dying;
2. an acknowledgement is lost after the side effect — the delivery stays
   pending, the handler is **not** re-run in the same pass, and a replay
   resolves it without duplicating the durable step row;
3. persistence fails once — the delivery is retried, the step lands once and
   the verdict is published once;
4. persistence stays broken — the delivery is parked in the DLQ with the
   reason, and a redrive after recovery finishes the work while keeping the
   parked record as evidence;
5. the process restarts — the run, its steps and its audit chain are read
   back from disk, verify, and match the anchor taken before the restart.

The limits are stated in the module: this is fault injection at the seam
between worker and broker/disk, not a process-kill loop over Redis and
Postgres (`docs/LIMITATIONS.md` §4 keeps that distinction).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from app.models import ExecutionStore, Recorder
from bus.backend import Backend, MemoryBackend
from bus.consumer import Runner
from bus.events import Event, new_event
from bus.triggers import trigger_file_arrived, trigger_manual
from bus.workers.core import RESULTS_STREAM, make_step_worker, steps_stream
from bus.workers.dispatcher import pump_workers
from storage.anchor import build_document, chain_heads, verify_document
from storage.audit import AuditStore

WORKFLOW = """workflow: chaos
version: 1
triggers:
  - manual: true
nodes:
  - id: a
    type: shell
    run: echo a
  - id: b
    type: shell
    run: echo b
edges: [[a, b]]
"""


class FlakyBackend(Backend):
    """Delegating backend with switched outages (chaos, not a mock).

    Each counter is the number of upcoming calls that fail with a broker
    `ConnectionError`; after that the call behaves normally, which is how a
    restart is modelled without a second process.
    """

    def __init__(self, inner: Backend | None = None) -> None:
        self.inner = inner or MemoryBackend()
        self.read_failures = 0
        self.ack_failures = 0
        self.publish_failures = 0

    async def publish(self, stream: str, event: Event) -> str:
        if self.publish_failures:
            self.publish_failures -= 1
            raise ConnectionError("broker unavailable")
        return await self.inner.publish(stream, event)

    async def ensure_group(self, stream: str, group: str) -> None:
        if self.read_failures:
            self.read_failures -= 1
            raise ConnectionError("broker unavailable")
        await self.inner.ensure_group(stream, group)

    async def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[tuple[str, Event]]:
        if self.read_failures:
            self.read_failures -= 1
            raise ConnectionError("broker unavailable")
        return await self.inner.read_group(stream, group, consumer, count)

    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        if self.ack_failures:
            self.ack_failures -= 1
            raise ConnectionError("acknowledgement lost")
        await self.inner.acknowledge(stream, group, message_id)

    async def pending_count(self, stream: str, group: str) -> int:
        return await self.inner.pending_count(stream, group)

    async def read_range(
        self, stream: str, start: int = 0, end: int | None = None
    ) -> list[tuple[str, Event]]:
        return await self.inner.read_range(stream, start, end)

    async def to_dlq(self, stream: str, event: Event, reason: str) -> str:
        return await self.inner.to_dlq(stream, event, reason)

    async def dlq_list(self, stream: str) -> list[tuple[str, Event, str]]:
        return await self.inner.dlq_list(stream)


def _dispatch(run_id: str, node_id: str = "a") -> Event:
    return new_event(
        "step.dispatched",
        {"node_id": node_id, "node": {"type": "shell", "params": {}}},
        correlation_id=run_id,
        idempotency_key=f"{run_id}:{node_id}",
    )


async def _finished(backend: Backend) -> list[Event]:
    return [
        event
        for _, event in await backend.read_range(RESULTS_STREAM)
        if event.type == "step.finished"
    ]


class BrokerOutageTests(unittest.TestCase):
    def test_worker_survives_a_broker_restart(self):
        async def scenario():
            backend = FlakyBackend()
            calls: list[str] = []

            async def exec_fn(node_id, node, ctx):
                calls.append(node_id)
                return {"ok": node_id}

            worker = make_step_worker(backend, "chaos", "shell", exec_fn, max_retries=1)
            await backend.publish(steps_stream("shell"), _dispatch("r1"))
            backend.read_failures = 2

            first = await worker.drain_once()
            second = await worker.drain_once()
            self.assertEqual((first.backend_errors, second.backend_errors), (1, 1))
            self.assertEqual(calls, [], "nothing may run while the broker is down")

            third = await worker.drain_once()
            self.assertEqual(third.processed, 1)
            self.assertEqual(calls, ["a"])
            self.assertEqual(len(await _finished(backend)), 1)

        asyncio.run(scenario())

    def test_serve_keeps_polling_through_an_outage(self):
        async def scenario():
            backend = FlakyBackend()
            backend.read_failures = 3
            worker = Runner(backend, "chaos", [steps_stream("shell")], max_retries=0)
            seen: list[str] = []

            @worker.on("step.dispatched")
            async def handle(event):
                seen.append(event.data["node_id"])

            await backend.publish(steps_stream("shell"), _dispatch("r2"))
            task = asyncio.create_task(worker.serve(poll_s=0.01))
            try:
                for _ in range(200):
                    if seen:
                        break
                    await asyncio.sleep(0.01)
            finally:
                worker.stop()
                totals = await asyncio.wait_for(task, timeout=5)
            self.assertEqual(seen, ["a"])
            self.assertGreaterEqual(totals.backend_errors, 1)
            self.assertEqual(totals.processed, 1)

        asyncio.run(scenario())

    def test_a_lost_plan_publish_is_safe_to_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "chaos.yaml").write_text(WORKFLOW, encoding="utf-8")

            async def scenario():
                backend = FlakyBackend()
                calls: list[str] = []

                async def exec_fn(node_id, node, ctx):
                    calls.append(node_id)
                    return {"ok": node_id}

                worker = make_step_worker(backend, "chaos", "shell", exec_fn)
                stop = asyncio.Event()
                pump = asyncio.create_task(pump_workers([worker], stop))
                try:
                    backend.publish_failures = 1
                    with self.assertRaises(ConnectionError):
                        await trigger_file_arrived(
                            backend, "chaos", workflows_dir=directory, run_id="run-retry"
                        )
                    self.assertEqual(calls, [], "a run that never started must not execute")
                    self.assertEqual(
                        await backend.read_range("plans"), [], "no half-published plan"
                    )

                    result = await trigger_file_arrived(
                        backend, "chaos", workflows_dir=directory, run_id="run-retry"
                    )
                finally:
                    stop.set()
                    await pump

                self.assertEqual(result.status, "COMPLETED")
                plans = [
                    event
                    for _, event in await backend.read_range("plans")
                    if event.type == "plan.created"
                ]
                self.assertEqual(len(plans), 1)
                self.assertEqual(sorted(calls), ["a", "b"])

            asyncio.run(scenario())


class LostAcknowledgementTests(unittest.TestCase):
    def test_lost_ack_does_not_rerun_the_handler_and_a_replay_resolves_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = init_db(Path(tmp) / "app.db")
            store = ExecutionStore(db)
            store.start("wf", 1, run_id="run-ack")

            async def scenario():
                calls: list[str] = []

                async def exec_fn(node_id, node, ctx):
                    calls.append(node_id)
                    return {"ok": node_id}

                def persist(run_id, node_id, state, error, output):
                    store.record_step(run_id, node_id, state, output_summary="{}")

                backend = FlakyBackend()
                worker = make_step_worker(
                    backend, "chaos", "shell", exec_fn, persist=persist, max_retries=1
                )
                backend.ack_failures = 1
                await backend.publish(steps_stream("shell"), _dispatch("run-ack"))

                stats = await worker.drain_once()
                self.assertEqual(stats.processed, 1)
                self.assertEqual(stats.backend_errors, 1)
                self.assertEqual(calls, ["a"], "the side effect runs once in this pass")
                self.assertEqual(
                    await backend.pending_count(steps_stream("shell"), "chaos"),
                    1,
                    "the unacknowledged delivery is retained by the broker",
                )
                self.assertEqual(len(store.get("run-ack")["steps"]), 1)

                replay = await worker.replay(steps_stream("shell"))
                self.assertEqual(replay.processed, 1)
                # The handler may run again (at-least-once), but the durable
                # step row and the verdict are idempotent: one of each.
                self.assertEqual(len(store.get("run-ack")["steps"]), 1)
                self.assertEqual(len(await _finished(backend)), 1)

            asyncio.run(scenario())


class StorageOutageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.store = ExecutionStore(self.db)
        self.store.start("wf", 1, run_id="run-disk")

    def test_transient_storage_failure_is_retried_not_acked_early(self):
        async def scenario():
            attempts: list[int] = []

            def persist(run_id, node_id, state, error, output):
                attempts.append(1)
                if len(attempts) == 1:
                    raise sqlite3.OperationalError("database is locked")
                self.store.record_step(run_id, node_id, state, output_summary="{}")

            async def exec_fn(node_id, node, ctx):
                return {"ok": node_id}

            backend = MemoryBackend()
            worker = make_step_worker(
                backend, "chaos", "shell", exec_fn, persist=persist, max_retries=2
            )
            await backend.publish(steps_stream("shell"), _dispatch("run-disk"))
            stats = await worker.drain_once()

            self.assertEqual(stats.errors, 1, "the first attempt failed and was retried")
            self.assertEqual(stats.processed, 1)
            self.assertEqual(stats.dlq, 0)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len(self.store.get("run-disk")["steps"]), 1)
            self.assertEqual(len(await _finished(backend)), 1)

        asyncio.run(scenario())

    def test_a_storage_outage_parks_the_work_and_a_redrive_finishes_it(self):
        async def scenario():
            broken = True

            def persist(run_id, node_id, state, error, output):
                if broken:
                    raise sqlite3.OperationalError("disk full")
                self.store.record_step(run_id, node_id, state, output_summary="{}")

            async def exec_fn(node_id, node, ctx):
                return {"ok": node_id}

            backend = MemoryBackend()
            worker = make_step_worker(
                backend, "chaos", "shell", exec_fn, persist=persist, max_retries=1
            )
            await backend.publish(steps_stream("shell"), _dispatch("run-disk"))

            stats = await worker.drain_once()
            self.assertEqual(stats.dlq, 1)
            parked = await backend.dlq_list(steps_stream("shell"))
            self.assertEqual(len(parked), 1)
            self.assertIn("disk full", parked[0][2])
            self.assertEqual(self.store.get("run-disk")["steps"], [])
            self.assertEqual(await _finished(backend), [])

            broken = False
            recovered = await worker.redrive_dlq(steps_stream("shell"))
            self.assertEqual(recovered.processed, 1)
            self.assertEqual(len(self.store.get("run-disk")["steps"]), 1)
            self.assertEqual(len(await _finished(backend)), 1)
            self.assertEqual(
                len(await backend.dlq_list(steps_stream("shell"))),
                1,
                "the parked entry is evidence of the outage, not a queue to drain",
            )

        asyncio.run(scenario())


class RestartTests(unittest.TestCase):
    def test_run_steps_and_audit_chain_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "chaos.yaml").write_text(WORKFLOW, encoding="utf-8")
            db = init_db(directory / "app.db")
            audit = AuditStore(db)

            class CapturingRecorder(Recorder):
                """Remembers the execution id so the step worker can persist."""

                execution_id = ""

                def start_execution(self, *args, **kwargs):
                    row = super().start_execution(*args, **kwargs)
                    self.execution_id = row.id
                    return row

            recorder = CapturingRecorder(db)
            reopened_steps = ExecutionStore(db)

            async def scenario():
                async def exec_fn(node_id, node, ctx):
                    return {"ok": node_id}

                def persist(run_id, node_id, state, error, output):
                    reopened_steps.record_step(
                        recorder.execution_id,
                        node_id,
                        state,
                        error=error,
                        output_summary=json.dumps(output, sort_keys=True),
                    )

                backend = MemoryBackend()
                worker = make_step_worker(backend, "chaos", "shell", exec_fn, persist=persist)
                stop = asyncio.Event()
                pump = asyncio.create_task(pump_workers([worker], stop))
                try:
                    result = await trigger_manual(
                        backend,
                        "chaos",
                        workflows_dir=directory,
                        run_id="run-restart",
                        recorder=recorder,
                        audit_store=audit,
                        workspace_root=directory / "workspaces",
                    )
                finally:
                    stop.set()
                    await pump
                self.assertEqual(result.status, "COMPLETED")
                self.assertEqual(
                    await backend.pending_count(steps_stream("shell"), "chaos"),
                    0,
                    "a completed run leaves no unacknowledged delivery",
                )

            asyncio.run(scenario())

            # Restart: nothing is reused, everything is read from the file.
            reopened = ExecutionStore(db)
            executions = reopened.list_executions()
            self.assertEqual(len(executions), 1)
            execution_id = str(executions[0]["id"])
            self.assertEqual(executions[0]["status"], "COMPLETED")

            reopened_audit = AuditStore(db)
            check = reopened_audit.verify_chain(execution_id)
            self.assertTrue(check.ok, check.reason)
            self.assertGreaterEqual(check.checked, 5)
            self.assertEqual(len(reopened.get(execution_id)["steps"]), 2)

            before = build_document(chain_heads(audit), created_at="2026-10-18T00:00:00Z")
            after = build_document(chain_heads(reopened_audit), created_at="2026-10-18T00:00:00Z")
            self.assertEqual(before["digest"], after["digest"])
            self.assertTrue(verify_document(before, chain_heads(reopened_audit)).ok)


if __name__ == "__main__":
    unittest.main()
