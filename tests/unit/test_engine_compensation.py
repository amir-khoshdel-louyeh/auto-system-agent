"""Phase 3 item [32]: compensation contract (saga rollback, FR-10).

After a kill/failure the caller undoes every node that reached OK, newest
first. Compensation never raises: a failing compensator is reported per node
so the audit trail stays complete, and nodes that never succeeded are left
alone.
"""

import asyncio
import unittest

from engine.executor.compensate import compensate
from engine.executor.result import OK, NodeResult, RunResult
from engine.executor.scheduler import run_dag


def _run(coro):
    return asyncio.run(coro)


class CompensationTests(unittest.TestCase):
    def _completed_run(self):
        async def handler(nid, spec):
            return {"ok": nid}

        dag = {"nodes": {"a": {}, "b": {}, "c": {}}, "edges": [("a", "b"), ("b", "c")]}
        return _run(run_dag(dag, handler, timeout_s=5))

    def test_compensation_undoes_nodes_in_reverse_order(self):
        run = self._completed_run()
        order: list[str] = []

        async def compensator(nid, output):
            order.append(nid)

        outcomes = _run(compensate(run, compensator))
        self.assertEqual([nid for nid, _ in outcomes], ["c", "b", "a"])
        self.assertEqual(order, ["c", "b", "a"])
        self.assertTrue(all(result == "OK" for _, result in outcomes))

    def test_compensation_failure_is_reported_not_raised(self):
        run = self._completed_run()

        async def compensator(nid, output):
            if nid == "b":
                raise RuntimeError("cannot undo b")

        outcomes = dict(_run(compensate(run, compensator)))
        self.assertEqual(outcomes["a"], "OK")
        self.assertEqual(outcomes["c"], "OK")
        self.assertTrue(outcomes["b"].startswith("FAILED:"))

    def test_only_ok_nodes_are_compensated(self):
        run = RunResult(run_id="r")
        run.order = ["a", "b"]
        run.nodes["a"] = NodeResult(node_id="a", status=OK, output={})
        run.nodes["b"] = NodeResult(node_id="b", status="FAILED", output={})
        undone: list[str] = []

        async def compensator(nid, output):
            undone.append(nid)

        outcomes = _run(compensate(run, compensator))
        self.assertEqual(outcomes, [("a", "OK")])
        self.assertEqual(undone, ["a"])

    def test_compensation_timeout_is_a_reported_failure(self):
        run = self._completed_run()

        async def slow_compensator(nid, output):
            await asyncio.sleep(30)

        outcomes = _run(compensate(run, slow_compensator, timeout_s=0.05))
        self.assertTrue(all(result.startswith("FAILED:") for _, result in outcomes))


if __name__ == "__main__":
    unittest.main()
