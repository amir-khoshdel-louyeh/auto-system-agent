"""Phase 5 item [51]: execution metrics (success, latency, retries, approvals).

Pure-function metrics so the reported numbers are deterministic and testable
without a live system.
"""

import unittest

from storage.run_metrics import RunMetrics, latency_ms, percentile, summarize

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-01-01T00:00:01+00:00"
T2 = "2026-01-01T00:00:02+00:00"
T3 = "2026-01-01T00:00:04+00:00"


class HelperTests(unittest.TestCase):
    def test_latency_from_timestamps(self):
        self.assertEqual(latency_ms({"started_at": T0, "finished_at": T1}), 1000.0)
        self.assertEqual(latency_ms({"started_at": T0, "finished_at": T3}), 4000.0)

    def test_latency_none_when_incomplete(self):
        self.assertIsNone(latency_ms({"started_at": T0, "finished_at": ""}))
        self.assertIsNone(latency_ms({}))

    def test_percentile_nearest_rank(self):
        self.assertEqual(percentile([], 95), 0.0)
        self.assertEqual(percentile([10, 20, 30, 40], 100), 40)
        self.assertEqual(percentile([10, 20, 30, 40], 50), 30)


class SummarizeTests(unittest.TestCase):
    def test_success_rate_and_latency(self):
        executions = [
            {"status": "COMPLETED", "started_at": T0, "finished_at": T1},
            {"status": "COMPLETED", "started_at": T0, "finished_at": T2},
            {"status": "FAILED", "started_at": T0, "finished_at": T3},
        ]
        metrics = summarize(executions)
        self.assertEqual(metrics.total, 3)
        self.assertEqual(metrics.succeeded, 2)
        self.assertEqual(metrics.failed, 1)
        self.assertAlmostEqual(metrics.success_rate, 66.67, places=2)
        self.assertAlmostEqual(metrics.avg_latency_ms, 2333.33, places=2)

    def test_retries_count_extra_attempts(self):
        steps = [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}, {"attempt": 1}]
        self.assertEqual(summarize([], steps=steps).retries, 2)

    def test_approval_time_from_request_to_first_decision(self):
        requests = [{"execution_id": "r1", "node_id": "gate", "created_at": T0}]
        decisions = [
            {"execution_id": "r1", "node_id": "gate", "decided_at": T3},
            {"execution_id": "r1", "node_id": "gate", "decided_at": T2},
        ]
        metrics = summarize([], requests=requests, decisions=decisions)
        self.assertEqual(metrics.approval_count, 1)
        self.assertAlmostEqual(metrics.avg_approval_s, 2.0, places=2)

    def test_empty_inputs_are_safe(self):
        metrics = summarize([])
        self.assertIsInstance(metrics, RunMetrics)
        self.assertEqual(metrics.total, 0)
        self.assertEqual(metrics.success_rate, 100.0)
        self.assertEqual(metrics.avg_latency_ms, 0.0)

    def test_as_dict_shape(self):
        keys = set(summarize([]).as_dict())
        self.assertEqual(
            keys,
            {
                "total",
                "succeeded",
                "failed",
                "success_rate",
                "avg_latency_ms",
                "p95_latency_ms",
                "retries",
                "approval_count",
                "avg_approval_s",
            },
        )


if __name__ == "__main__":
    unittest.main()
