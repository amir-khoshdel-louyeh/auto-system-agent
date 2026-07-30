"""Phase 3 item [30]: independent nodes execute concurrently (FR-10).

The scheduler groups nodes into Kahn levels and runs each level with
``asyncio.gather``; this pins the observable property: same-level nodes
overlap in wall-clock time, while a dependent node waits for its parent.
"""

import asyncio
import time
import unittest

from engine.executor.scheduler import compute_levels, run_dag


def _run(coro):
    return asyncio.run(coro)


class ConcurrencyTests(unittest.TestCase):
    def test_levels_group_independent_nodes(self):
        nodes = {"a": {}, "b": {}, "c": {}}
        levels = compute_levels(nodes, [("a", "c"), ("b", "c")])
        self.assertEqual(levels, [["a", "b"], ["c"]])

    def test_three_independent_nodes_overlap(self):
        async def handler(nid, spec):
            await asyncio.sleep(0.2)
            return {"ok": nid}

        dag = {"nodes": {"a": {}, "b": {}, "c": {}}, "edges": []}
        started = time.monotonic()
        result = _run(run_dag(dag, handler, timeout_s=5))
        elapsed = time.monotonic() - started
        self.assertEqual(result.status, "COMPLETED")
        self.assertLess(elapsed, 0.5, "independent nodes must overlap, not serialize")

    def test_dependent_node_waits_for_its_parent(self):
        timeline: list[tuple[str, str]] = []

        async def handler(nid, spec):
            timeline.append((nid, "start"))
            await asyncio.sleep(0.05)
            timeline.append((nid, "end"))
            return {"ok": nid}

        dag = {"nodes": {"a": {}, "b": {}}, "edges": [("a", "b")]}
        result = _run(run_dag(dag, handler, timeout_s=5))
        self.assertEqual(result.status, "COMPLETED")
        self.assertLess(
            timeline.index(("a", "end")),
            timeline.index(("b", "start")),
            "dependent node must start after its parent ends",
        )


if __name__ == "__main__":
    unittest.main()
