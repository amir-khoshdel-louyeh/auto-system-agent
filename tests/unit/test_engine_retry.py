"""Phase 3 item [31]: timeout, retry and failure propagation (FR-10).

A node that times out or exhausts its retries is FAILED; its dependents are
never attempted (BLOCKED/SKIPPED); a clean run reaches COMPLETED.
"""

import asyncio
import unittest

from engine.executor.result import OK
from engine.executor.scheduler import run_dag


def _run(coro):
    return asyncio.run(coro)


class TimeoutRetryPropagationTests(unittest.TestCase):
    def test_timeout_fails_node_and_blocks_dependents(self):
        async def slow(nid, spec):
            if nid == "a":
                await asyncio.sleep(30)
            return {}

        dag = {"nodes": {"a": {}, "b": {}}, "edges": [("a", "b")]}
        result = _run(run_dag(dag, slow, timeout_s=0.1))
        self.assertEqual(result.nodes["a"].status, "FAILED")
        self.assertIn("timeout", result.nodes["a"].error)
        self.assertEqual(result.nodes["b"].status, "SKIPPED")

    def test_retries_are_bounded_then_fail(self):
        calls = {"n": 0}

        async def always_fail(nid, spec):
            calls["n"] += 1
            raise RuntimeError("boom")

        dag = {"nodes": {"a": {}}, "edges": []}
        result = _run(run_dag(dag, always_fail, timeout_s=5, max_retries=2))
        self.assertEqual(result.nodes["a"].status, "FAILED")
        self.assertEqual(result.nodes["a"].attempts, 3)  # 1 try + 2 retries
        self.assertEqual(calls["n"], 3)

    def test_retry_then_success_is_recorded(self):
        calls = {"n": 0}

        async def flaky(nid, spec):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return {"ok": True}

        dag = {"nodes": {"a": {}}, "edges": []}
        result = _run(run_dag(dag, flaky, timeout_s=5, max_retries=3))
        self.assertEqual(result.nodes["a"].status, OK)
        self.assertEqual(result.nodes["a"].attempts, 3)

    def test_failure_propagates_to_all_dependents(self):
        async def handler(nid, spec):
            if nid == "b":
                raise RuntimeError("install killed")
            return {"ok": nid}

        dag = {
            "nodes": {"a": {}, "b": {}, "c": {}, "d": {}},
            "edges": [("a", "b"), ("b", "c"), ("c", "d")],
        }
        result = _run(run_dag(dag, handler, timeout_s=5))
        self.assertEqual(result.nodes["a"].status, OK)
        self.assertEqual(result.nodes["b"].status, "FAILED")
        self.assertEqual(result.nodes["c"].status, "SKIPPED")
        self.assertEqual(result.nodes["d"].status, "SKIPPED")

    def test_all_ok_run_is_completed(self):
        async def handler(nid, spec):
            return {"ok": nid}

        dag = {"nodes": {"a": {}, "b": {}}, "edges": [("a", "b")]}
        self.assertEqual(_run(run_dag(dag, handler)).status, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
