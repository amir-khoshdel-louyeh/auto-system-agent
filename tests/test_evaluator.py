import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.orchestration.evaluator import Evaluator
from auto_system_agent.models import ExecutionResult, PlannedTask, ReActStep


def _task(cmd: str) -> PlannedTask:
    return PlannedTask(action="run_command", target=cmd, raw_input=cmd)


class EvaluatorTests(unittest.TestCase):
    def test_success_is_done(self):
        ev = Evaluator().evaluate("list files", _task("ls"), "run_command", ExecutionResult(True, "ok"))
        self.assertEqual(ev.verdict, "done")

    def test_unknown_tool_needs_replan(self):
        ev = Evaluator().evaluate("x", _task("x"), "unknown", ExecutionResult(False, "nope"))
        self.assertEqual(ev.verdict, "replan")

    def test_missing_file_needs_replan(self):
        result = ExecutionResult(False, "ls: no such file or directory: /tmp/xyz")
        ev = Evaluator().evaluate("list", _task("ls /tmp/xyz"), "run_command", result)
        self.assertEqual(ev.verdict, "replan")

    def test_permission_denied_aborts(self):
        result = ExecutionResult(False, "permission denied")
        ev = Evaluator().evaluate("x", _task("cat /root/f"), "run_command", result)
        self.assertEqual(ev.verdict, "abort")

    def test_user_cancel_aborts(self):
        result = ExecutionResult(False, "Cancelled by user.")
        ev = Evaluator().evaluate("x", _task("sleep 60"), "run_command", result)
        self.assertEqual(ev.verdict, "abort")

    def test_generic_failure_retries(self):
        result = ExecutionResult(False, "something broke")
        ev = Evaluator().evaluate("x", _task("false"), "run_command", result)
        self.assertEqual(ev.verdict, "retry")
        self.assertEqual(ev.fixed_command, "false")

    def test_repeated_failures_escalate_to_replan(self):
        evaluator = Evaluator(max_same_command_retries=2)
        scratch = [
            ReActStep(thought="t", task=_task("false"), tool="run_command", result=ExecutionResult(False, "bad")),
            ReActStep(thought="t", task=_task("false"), tool="run_command", result=ExecutionResult(False, "bad")),
        ]
        ev = evaluator.evaluate("x", _task("false"), "run_command", ExecutionResult(False, "bad"), scratch)
        self.assertEqual(ev.verdict, "replan")


if __name__ == "__main__":
    unittest.main()
