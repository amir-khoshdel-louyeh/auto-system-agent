"""P4 unit coverage: metrics, templates, notifications, evaluator branches."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.metrics import (
    Metric,
    MetricSampler,
    avg_cpu,
    cpu_percent_between,
    mttr,
    read_disk_percent,
    read_mem_percent,
    success_rate,
    throughput,
)


class MetricsTests(unittest.TestCase):
    def test_cpu_delta_math(self):
        self.assertEqual(cpu_percent_between((100, 1000), (150, 1200)), 75.0)
        self.assertEqual(cpu_percent_between((0, 0), (0, 0)), 0.0)
        self.assertEqual(cpu_percent_between((200, 1000), (100, 1200)), 0.0)

    def test_live_readers_sane(self):
        self.assertGreaterEqual(read_mem_percent(), 0.0)
        self.assertLessEqual(read_mem_percent(), 100.0)
        self.assertGreaterEqual(read_disk_percent("/tmp"), 0.0)
        self.assertEqual(read_disk_percent("/nonexistent_path_12345"), 0.0)

    def test_sampler_first_zero_then_delta(self):
        sampler = MetricSampler(disk_path="/tmp")
        first = sampler.sample()
        self.assertEqual(first.cpu, 0.0)
        second = sampler.sample(queue_depth=4)
        self.assertGreaterEqual(second.cpu, 0.0)
        self.assertEqual(second.queue, 4)

    def test_formulae_edges(self):
        rows = [Metric(ts=100.0, cpu=10.0), Metric(ts=130.0, cpu=20.0), Metric(ts=200.0, cpu=30.0)]
        self.assertEqual(avg_cpu(rows, 75), 25.0)
        self.assertEqual(avg_cpu(rows, 5), 30.0)
        self.assertEqual(avg_cpu([], 60), 0.0)
        self.assertEqual(avg_cpu(rows, 0), 0.0)
        self.assertEqual(success_rate(9, 10), 90.0)
        self.assertEqual(success_rate(0, 0), 100.0)
        self.assertEqual(mttr([(0, 10), (20, 30)]), 10.0)
        self.assertEqual(mttr([]), 0.0)
        self.assertEqual(throughput(120, 60), 2.0)
        self.assertEqual(throughput(5, 0), 0.0)


class TemplateTests(unittest.TestCase):
    def test_save_replay_and_rates(self):
        import tempfile

        from auto_system_agent.templates import TemplateStore, validate_plan

        with tempfile.TemporaryDirectory() as tmp:
            store = TemplateStore(db_path=Path(tmp) / "audit.db")
            template = store.save(
                "demo",
                [
                    {"action": "run_command", "target": "mkdir -p demo"},
                    {"action": "run_command", "target": "touch demo/a.txt"},
                ],
            )
            self.assertEqual(template.runs, 0)
            tasks = store.replay("demo", raw_input="make demo")
            self.assertEqual([(t.action, t.target) for t in tasks], [("run_command", "mkdir -p demo"), ("run_command", "touch demo/a.txt")])
            self.assertEqual(store.record_run("demo", True).success_rate, 100.0)
            self.assertEqual(store.record_run("demo", False).success_rate, 50.0)
            self.assertEqual(store.list_names(), ["demo"])

    def test_invalid_plans_rejected(self):
        from auto_system_agent.templates import TemplateStore

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = TemplateStore(db_path=Path(tmp) / "audit.db")
            for steps in (
                [],
                [{"action": "run_command", "target": "rm -rf /"}],
                [{"action": "nuke", "target": "x"}],
                [{"action": "run_command", "target": ""}],
            ):
                with self.subTest(steps=steps):
                    with self.assertRaises(ValueError):
                        store.save("bad", steps)
            with self.assertRaises(ValueError):
                store.save("", [{"action": "help", "target": ""}])
            with self.assertRaises(KeyError):
                store.replay("missing")
            with self.assertRaises(KeyError):
                store.record_run("missing", True)


class NotificationTests(unittest.TestCase):
    def test_notify_never_raises(self):
        from auto_system_agent.notifications import ResultCache, notify, result_key

        self.assertFalse(notify("t", ""))
        self.assertIsInstance(notify("hello", "world", os_name="linux"), bool)
        self.assertIsInstance(notify("hello", "world", os_name="plan9"), bool)

    def test_result_cache_bounds(self):
        from auto_system_agent.notifications import ResultCache, result_key

        cache = ResultCache(capacity=2)
        cache.put(result_key("ls", 0), "a")
        cache.put(result_key("pwd", 0), "b")
        cache.put(result_key("who", 0), "c")
        self.assertEqual(len(cache), 2)
        self.assertNotIn(result_key("ls", 0), cache)
        self.assertEqual(cache.get(result_key("who", 0)), "c")
        self.assertIsNone(cache.get("missing"))
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_backend_selection_with_mocked_platforms(self):
        from unittest.mock import patch

        from auto_system_agent import notifications

        class Done:
            returncode = 0

        class Failed:
            returncode = 1

        with patch.object(notifications.shutil, "which", return_value=None):
            self.assertFalse(notifications.notify("t", "m", os_name="linux"))
            self.assertFalse(notifications.notify("t", "m", os_name="macos"))
            self.assertFalse(notifications.notify("t", "m", os_name="windows"))
        with patch.object(notifications.shutil, "which", return_value="/usr/bin/x"), patch.object(
            notifications.subprocess, "run", return_value=Done()
        ):
            self.assertTrue(notifications.notify("t", "m", os_name="linux"))
            self.assertTrue(notifications.notify("t", "m", os_name="macos"))
            self.assertTrue(notifications.notify("t", "m", os_name="windows"))
        with patch.object(notifications.shutil, "which", return_value="/usr/bin/x"), patch.object(
            notifications.subprocess, "run", return_value=Failed()
        ):
            self.assertFalse(notifications.notify("t", "m", os_name="linux"))
        with patch.object(
            notifications.subprocess, "run", side_effect=OSError("no exec")
        ), patch.object(notifications.shutil, "which", return_value="/usr/bin/x"):
            self.assertFalse(notifications.notify("t", "m", os_name="linux"))


class EvaluatorBranchTests(unittest.TestCase):
    def _evaluate(self, message, success=False, scratchpad=None):
        from auto_system_agent.evaluator import Evaluator
        from auto_system_agent.models import ExecutionResult, PlannedTask

        return Evaluator().evaluate(
            "goal",
            PlannedTask(action="run_command", target="ls /nope"),
            "run_command",
            ExecutionResult(success=success, message=message),
            scratchpad or [],
        )

    def test_already_exists_is_done(self):
        self.assertEqual(self._evaluate("mkdir: file exists", True).verdict, "done")

    def test_cancelled_is_abort(self):
        self.assertEqual(self._evaluate("Cancelled by user.").verdict, "abort")

    def test_llm_verdict_parsing(self):
        from auto_system_agent.evaluator import Evaluator

        parsed = Evaluator._parse_llm_verdict('{"verdict": "retry", "reason": "typo", "fixed_command": "ls"}')
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.verdict, "retry")
        self.assertIsNone(Evaluator._parse_llm_verdict("no json here"))
        self.assertIsNone(Evaluator._parse_llm_verdict('{"verdict": "explode"}'))
        self.assertIsNone(Evaluator._parse_llm_verdict(""))

    def test_empty_command_never_repeats(self):
        from auto_system_agent.evaluator import Evaluator

        self.assertFalse(Evaluator()._repeated_failures("", []))


if __name__ == "__main__":
    unittest.main()
