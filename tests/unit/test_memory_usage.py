"""Phase 7 item [72]: reuse-hit and no-generation-cost metrics.

A reuse hit is a planning request answered from memory, so it costs zero
planner generations; a miss costs exactly one. The metrics are a pure
function over recorded rows, and the recorded row never keeps the raw query.
"""

import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from memory.usage import (
    list_retrievals,
    query_hash,
    record_retrieval,
    summarize,
)


class SummarizeTests(unittest.TestCase):
    def test_empty_history_has_zero_rates(self):
        metrics = summarize([])
        self.assertEqual(metrics.requests, 0)
        self.assertEqual(metrics.reuse_hits, 0)
        self.assertEqual(metrics.hit_rate, 0.0)
        self.assertEqual(metrics.no_generation_rate, 0.0)
        self.assertEqual(metrics.planner_generations, 0)
        self.assertEqual(metrics.planner_calls_avoided, 0)
        self.assertEqual(metrics.avg_hit_score, 0.0)

    def test_hits_avoid_generation_and_misses_do_not(self):
        rows = [
            {"hit": 1, "score": 0.9},
            {"hit": 1, "score": 0.7},
            {"hit": 0, "score": 0.0},
            {"hit": 0, "score": 0.0},
        ]
        metrics = summarize(rows)
        self.assertEqual(metrics.requests, 4)
        self.assertEqual(metrics.reuse_hits, 2)
        self.assertEqual(metrics.memory_misses, 2)
        self.assertEqual(metrics.hit_rate, 0.5)
        self.assertEqual(metrics.no_generation_rate, 0.5)
        self.assertEqual(metrics.planner_calls_avoided, 2)
        self.assertEqual(metrics.planner_generations, 2)
        self.assertEqual(metrics.avg_hit_score, 0.8)

    def test_all_hits_never_generate(self):
        metrics = summarize([{"hit": 1, "score": 1.0}] * 3)
        self.assertEqual(metrics.hit_rate, 1.0)
        self.assertEqual(metrics.no_generation_rate, 1.0)
        self.assertEqual(metrics.planner_generations, 0)

    def test_as_dict_exposes_the_contract(self):
        keys = set(summarize([]).as_dict())
        self.assertEqual(
            keys,
            {
                "requests",
                "reuse_hits",
                "memory_misses",
                "hit_rate",
                "no_generation_rate",
                "planner_generations",
                "planner_calls_avoided",
                "avg_hit_score",
            },
        )


class RecordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "memory.db")

    def test_records_round_trip_and_never_store_the_raw_query(self):
        record_retrieval(self.db, query="install firefox", hit=True, score=0.91, hit_id="abc")
        record_retrieval(self.db, query="install firefox", hit=False)
        rows = list_retrievals(self.db)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("query" not in row for row in rows))
        self.assertEqual({row["query_hash"] for row in rows}, {query_hash("install firefox")})
        self.assertEqual({row["query_length"] for row in rows}, {len("install firefox")})

    def test_summarize_reads_persisted_rows(self):
        record_retrieval(self.db, query="q1", hit=True, score=0.5)
        record_retrieval(self.db, query="q2", hit=False)
        record_retrieval(self.db, query="q3", hit=False)
        metrics = summarize(list_retrievals(self.db))
        self.assertEqual(metrics.reuse_hits, 1)
        self.assertEqual(metrics.planner_generations, 2)
        self.assertAlmostEqual(metrics.hit_rate, 0.3333, places=4)

    def test_limit_keeps_the_most_recent_rows(self):
        for index in range(5):
            record_retrieval(
                self.db, query=f"query {index}", hit=False, now=f"2026-10-17T00:0{index}:00+00:00"
            )
        rows = list_retrievals(self.db, limit=2)
        self.assertEqual(
            [row["created_at"] for row in rows],
            [
                "2026-10-17T00:04:00+00:00",
                "2026-10-17T00:03:00+00:00",
            ],
        )

    def test_query_hash_is_stable_and_distinguishes_text(self):
        self.assertEqual(query_hash("abc"), query_hash("abc"))
        self.assertNotEqual(query_hash("abc"), query_hash("abd"))
        self.assertEqual(len(query_hash("abc")), 64)


if __name__ == "__main__":
    unittest.main()
