"""Post-RC item [100]: the Prometheus exposition, asserted line by line.

The exporter is a pure function over the same aggregate dict
`storage.run_metrics.summarize` produces, so the text a scraper reads is
pinned here instead of being eyeballed in a browser.
"""

import unittest

from app.metrics_export import CONTENT_TYPE, escape_label, render

METRICS = {
    "total": 4,
    "succeeded": 3,
    "failed": 1,
    "success_rate": 75.0,
    "avg_latency_ms": 12.5,
    "p95_latency_ms": 30.0,
    "retries": 2,
    "approval_count": 1,
    "avg_approval_s": 8.0,
}


class MetricsExportTests(unittest.TestCase):
    def test_every_run_metric_is_rendered_with_its_type_and_help(self):
        text = render(METRICS)
        self.assertIn("# TYPE asa_runs_total gauge", text)
        self.assertIn("asa_runs_total 4", text)
        self.assertIn("# TYPE asa_runs_succeeded_total counter", text)
        self.assertIn("asa_runs_failed_total 1", text)
        self.assertIn("asa_run_success_rate 75.0", text)
        self.assertIn("asa_run_latency_average_milliseconds 12.5", text)
        self.assertIn("asa_run_latency_p95_milliseconds 30.0", text)
        self.assertIn("asa_step_retries_total 2", text)
        self.assertIn("asa_approval_requests_total 1", text)
        self.assertIn("asa_approval_average_seconds 8.0", text)
        self.assertTrue(text.endswith("\n"), "exposition must end with a newline")

    def test_help_and_type_are_emitted_once_per_family(self):
        text = render(METRICS, memory={"items_by_state": {"PUBLIC": 2, "QUARANTINED": 1}})
        self.assertEqual(text.count("# HELP asa_memory_items "), 1)
        self.assertEqual(text.count("# TYPE asa_memory_items gauge"), 1)

    def test_memory_samples_carry_a_state_label(self):
        text = render(
            METRICS,
            memory={
                "items_by_state": {"QUARANTINED": 1, "PUBLIC": 2},
                "reuse_hits": 5,
                "hit_rate": 55.5,
            },
        )
        self.assertIn('asa_memory_items{state="PUBLIC"} 2', text)
        self.assertIn('asa_memory_items{state="QUARANTINED"} 1', text)
        self.assertIn("asa_memory_reuse_hits_total 5", text)
        self.assertIn("asa_memory_reuse_hit_rate 55.5", text)

    def test_build_info_is_a_labelled_constant(self):
        text = render(METRICS, build={"version": "1.2.3", "environment": "production"})
        self.assertIn(
            'asa_build_info{environment="production",version="1.2.3"} 1',
            text,
        )

    def test_label_values_are_escaped(self):
        self.assertEqual(escape_label('a"b\\c\nd'), 'a\\"b\\\\c\\nd')
        text = render(METRICS, build={"version": 'v"1', "environment": "a\nb"})
        self.assertIn('version="v\\"1"', text)
        self.assertIn('environment="a\\nb"', text)

    def test_missing_metrics_render_as_zero_not_as_absent_keys(self):
        text = render({})
        self.assertIn("asa_runs_total 0", text)
        self.assertIn("asa_run_success_rate 0.0", text)

    def test_content_type_is_the_prometheus_version(self):
        self.assertEqual(CONTENT_TYPE, "text/plain; version=0.0.4; charset=utf-8")


if __name__ == "__main__":
    unittest.main()
