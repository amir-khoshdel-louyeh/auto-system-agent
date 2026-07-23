"""Phase 2 (local-only, not committed): executor + gate + audit tests."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from app.db import init_db  # noqa: E402
from engine.executor.compensate import compensate  # noqa: E402
from engine.executor.gate import decide  # noqa: E402
from engine.executor.result import RunCancelled  # noqa: E402
from engine.executor.scheduler import compute_levels, run_dag  # noqa: E402
from storage.audit import AuditStore, audit_sink  # noqa: E402

def run(coro):
    return asyncio.run(coro)

class SchedulerTests(unittest.TestCase):
    def test_three_node_parallel_dag(self):
        async def handler(nid, spec):
            await asyncio.sleep(0.2)
            return {"ok": nid}

        dag = {
            "nodes": {"a": {"type": "shell"}, "b": {"type": "shell"}, "c": {"type": "shell"}},
            "edges": [("a", "c"), ("b", "c")],
        }
        self.assertEqual(compute_levels(dag["nodes"], dag["edges"]), [["a", "b"], ["c"]])
        started = time.monotonic()
        result = run(run_dag(dag, handler, timeout_s=5))
        elapsed = time.monotonic() - started
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.order, ["a", "b", "c"])
        self.assertLess(elapsed, 0.55)  # sequential would take >= 0.6s

    def test_retry_then_ok(self):
        calls = {"n": 0}

        async def flaky(nid, spec):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("boom")
            return {"ok": True}

        dag = {"nodes": {"a": {"type": "shell"}}, "edges": []}
        result = run(run_dag(dag, flaky, timeout_s=5, max_retries=3))
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.nodes["a"].attempts, 3)

    def test_fail_skips_downstream(self):
        async def handler(nid, spec):
            if nid == "b":
                raise RuntimeError("install killed")
            return {"ok": nid}

        dag = {"nodes": {"a": {}, "b": {}, "c": {}},
               "edges": [("a", "b"), ("b", "c")]}
        result = run(run_dag(dag, handler, timeout_s=5))
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.nodes["a"].status, "OK")
        self.assertEqual(result.nodes["b"].status, "FAILED")
        self.assertEqual(result.nodes["c"].status, "SKIPPED")

    def test_timeout(self):
        async def slow(nid, spec):
            await asyncio.sleep(30)
            return {}

        result = run(run_dag({"nodes": {"a": {}}, "edges": []}, slow, timeout_s=0.1))
        self.assertEqual(result.nodes["a"].status, "FAILED")
        self.assertIn("timeout", result.nodes["a"].error)

    def test_cycle_rejected_before_any_run(self):
        called = []

        async def handler(nid, spec):
            called.append(nid)
            return {}

        dag = {"nodes": {"a": {}, "b": {}},
               "edges": [("a", "b"), ("b", "a")]}
        with self.assertRaises(ValueError):
            run(run_dag(dag, handler))
        self.assertEqual(called, [])

class GateTests(unittest.TestCase):
    def test_deny_blocks_node_and_downstream(self):
        async def handler(nid, spec):
            return {"ok": nid}  # must never run for the denied node

        dag = {
            "nodes": {"wipe": {"type": "shell", "run": "rm -rf /"},
                      "next": {"type": "shell", "run": "echo hi"}},
            "edges": [("wipe", "next")],
        }
        result = run(run_dag(dag, handler, gate=decide))
        self.assertEqual(result.nodes["wipe"].status, "FAILED")
        self.assertEqual(result.nodes["wipe"].error, "policy DENY")
        self.assertEqual(result.nodes["next"].status, "SKIPPED")

    def test_allow_passes_safe_shell(self):
        dag = {"nodes": {"a": {"type": "shell", "run": "echo hi"}}, "edges": []}
        result = run(run_dag(dag, lambda nid, spec: _ok(), gate=decide))
        self.assertEqual(result.status, "COMPLETED")

    def test_gui_needs_approval_until_phase6(self):
        dag = {"nodes": {"g": {"type": "gui_click", "target": "x"}}, "edges": []}
        result = run(run_dag(dag, lambda nid, spec: _ok(), gate=decide))
        self.assertEqual(result.nodes["g"].status, "SKIPPED")
        self.assertIn("approval", result.nodes["g"].error)

def _ok():
    async def _inner():
        return {"ok": True}

    return _inner()

class KillCompensateTests(unittest.TestCase):
    def test_kill_mid_run_then_compensate(self):
        async def handler(nid, spec):
            if nid == "slow":
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(0.02)
            return {"done": nid}

        async def scenario():
            dag = {"nodes": {"fast": {}, "slow": {}}, "edges": []}
            task = asyncio.create_task(run_dag(dag, handler, timeout_s=30))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
                self.fail("expected cancellation")
            except RunCancelled as exc:
                return exc.run

        partial = run(scenario())
        self.assertEqual(partial.status, "CANCELLED")
        self.assertEqual(partial.nodes["fast"].status, "OK")
        undone = []

        async def compensator(nid, output):
            undone.append(nid)

        outcomes = run(compensate(partial, compensator))
        self.assertEqual(outcomes, [("fast", "OK")])
        self.assertEqual(undone, ["fast"])

class ChaosTests(unittest.TestCase):
    def test_chaos_failure_is_data_and_audited(self):
        db = init_db(Path(tempfile.mkdtemp()) / "chaos.db")
        store = AuditStore(db)

        async def handler(nid, spec):
            if nid == "poison":
                raise ValueError("x" * 5000)  # oversized error must truncate
            return {"ok": nid}

        dag = {"nodes": {"poison": {}, "after": {}},
               "edges": [("poison", "after")]}
        result = run(
            run_dag(dag, handler, timeout_s=5, on_event=audit_sink(store, "chaos-1"))
        )
        self.assertEqual(result.status, "FAILED")
        self.assertLessEqual(len(result.nodes["poison"].error), 2000)
        self.assertEqual(result.nodes["after"].status, "SKIPPED")
        self.assertTrue(store.verify("chaos-1"))
        names = [row["event"] for row in store.events("chaos-1")]
        self.assertIn("run.finished", names)

if __name__ == "__main__":
    unittest.main()
