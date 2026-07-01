"""P3.5: FSM transition coverage, chain hashing, timeouts, compensation."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_TEST_HOME = None
_OLD_HOME = None
_DB_PATH_BLOCK = None


def setUpModule():
    """No writes outside a temp HOME in these tests."""
    global _TEST_HOME, _OLD_HOME, _DB_PATH_BLOCK
    _TEST_HOME = tempfile.TemporaryDirectory()
    _OLD_HOME = os.environ.get("HOME")
    os.environ["HOME"] = _TEST_HOME.name
    _DB_PATH_BLOCK = patch(
        "auto_system_agent.repository.DEFAULT_DB_PATH", Path(_TEST_HOME.name) / "audit.db"
    )
    _DB_PATH_BLOCK.start()


def tearDownModule():
    global _TEST_HOME, _OLD_HOME, _DB_PATH_BLOCK
    if _DB_PATH_BLOCK is not None:
        _DB_PATH_BLOCK.stop()
    if _OLD_HOME is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _OLD_HOME
    if _TEST_HOME is not None:
        _TEST_HOME.cleanup()

from auto_system_agent.agent import (
    CHAIN_GENESIS,
    RUN_TRANSITIONS,
    InvalidTransition,
    RunState,
    StateMachine,
    chain_step_hash,
)


class TransitionTableTests(unittest.TestCase):
    def test_all_states_present(self):
        self.assertEqual(
            set(RUN_TRANSITIONS),
            {
                "IDLE", "PLANNED", "GUARDED", "EXECUTING", "OBSERVING",
                "REPAIRING", "COMPENSATING", "DONE", "FAILED",
            },
        )

    def test_happy_path_trail(self):
        fsm = StateMachine()
        for state in ("PLANNED", "GUARDED", "EXECUTING", "OBSERVING", "DONE"):
            fsm.advance(state)
        self.assertEqual(fsm.current, RunState.DONE)
        self.assertEqual(
            fsm.trail,
            ["IDLE", "PLANNED", "GUARDED", "EXECUTING", "OBSERVING", "DONE"],
        )

    def test_abort_path_through_compensating(self):
        fsm = StateMachine()
        for state in ("PLANNED", "GUARDED", "EXECUTING", "OBSERVING", "COMPENSATING", "FAILED"):
            fsm.advance(state)
        self.assertEqual(fsm.current, RunState.FAILED)

    def test_repair_loop_returns_to_guarded(self):
        fsm = StateMachine()
        for state in ("PLANNED", "GUARDED", "EXECUTING", "OBSERVING", "REPAIRING", "GUARDED"):
            fsm.advance(state)
        self.assertEqual(fsm.current, RunState.GUARDED)

    ILLEGAL_JUMPS = [
        ("IDLE", "EXECUTING"),
        ("IDLE", "DONE"),
        ("PLANNED", "EXECUTING"),
        ("GUARDED", "DONE"),
        ("GUARDED", "OBSERVING"),
        ("EXECUTING", "DONE"),
        ("OBSERVING", "IDLE"),
        ("OBSERVING", "PLANNED"),
        ("REPAIRING", "DONE"),
        ("REPAIRING", "EXECUTING"),
        ("COMPENSATING", "EXECUTING"),
        ("DONE", "EXECUTING"),
        ("FAILED", "GUARDED"),
    ]

    def test_illegal_jumps_rejected(self):
        for start, jump in self.ILLEGAL_JUMPS:
            with self.subTest(start=start, jump=jump):
                fsm = StateMachine(initial=start)
                self.assertFalse(fsm.can(jump))
                with self.assertRaises(InvalidTransition):
                    fsm.advance(jump)
                self.assertEqual(fsm.current, start)

    def test_reset_returns_to_idle(self):
        fsm = StateMachine()
        fsm.advance("PLANNED")
        fsm.reset()
        self.assertEqual(fsm.current, RunState.IDLE)
        self.assertTrue(fsm.can("PLANNED"))

    def test_verdict_hook_mapping(self):
        from auto_system_agent.agent import AutoSystemAgent, VERDICT_TRANSITIONS

        self.assertEqual(
            VERDICT_TRANSITIONS,
            {"done": None, "retry": "REPAIRING", "replan": "REPAIRING", "abort": "COMPENSATING"},
        )
        self.assertIsNone(AutoSystemAgent.transition_for_verdict("done"))
        self.assertEqual(AutoSystemAgent.transition_for_verdict("retry"), RunState.REPAIRING)
        self.assertEqual(AutoSystemAgent.transition_for_verdict("replan"), RunState.REPAIRING)
        self.assertEqual(AutoSystemAgent.transition_for_verdict("abort"), RunState.COMPENSATING)


class ChainHashTests(unittest.TestCase):
    def test_genesis_and_linkage(self):
        first = chain_step_hash(CHAIN_GENESIS, "mkdir -p demo", 0)
        second = chain_step_hash(first, "touch demo/a.txt", 0)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, second)
        # Deterministic material: sha256(prev + cmd + exit).
        import hashlib

        self.assertEqual(
            first, hashlib.sha256(f"{CHAIN_GENESIS}\nmkdir -p demo\n0".encode()).hexdigest()
        )

    def test_exit_code_changes_hash(self):
        prev = chain_step_hash(CHAIN_GENESIS, "ls", 0)
        self.assertNotEqual(
            chain_step_hash(prev, "ls /nope", 0), chain_step_hash(prev, "ls /nope", 2)
        )


class StepTimeoutTests(unittest.TestCase):
    def test_timeout_selection(self):
        from auto_system_agent.safe_executor import step_timeout

        cases = [
            ("ls -la /tmp", 300),
            ("mkdir -p demo", 300),
            ("sudo apt install -y vlc", 120),
            ("sudo -u root ls /tmp", 120),
            ("su -c ls", 120),
            ("/usr/bin/sudo ls", 120),
            ("echo 'unclosed", 300),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(step_timeout(command), expected)


class CompensationMapTests(unittest.TestCase):
    def test_compensation_commands(self):
        from auto_system_agent.safe_executor import compensation_for

        cases = [
            ("mkdir -p demo", "rmdir demo"),
            ("sudo mkdir -p /tmp/x", "sudo rmdir /tmp/x"),
            ("touch demo/a.txt", "rm demo/a.txt"),
            ("sudo apt install -y vlc", "sudo apt remove -y vlc"),
            ("sudo dnf install -y vlc", "sudo dnf remove -y vlc"),
            ("sudo pacman -S --noconfirm vlc", "sudo pacman -Rns vlc"),
            ("zip -r demo.zip demo", "rm demo.zip"),
            ("ls -la /tmp", None),
            ("rm demo/a.txt", None),
            ("mkdir -p a && touch a/b", None),
            ("touch", None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(compensation_for(command), expected)


class CompensateExecutorTests(unittest.TestCase):
    def test_compensate_reverses_and_stays_idempotent(self):
        import tempfile

        from auto_system_agent.models import PlannedTask
        from auto_system_agent.safe_executor import SafeExecutor

        with tempfile.TemporaryDirectory() as tmp:
            executor = SafeExecutor()
            try:
                executor.terminal._cwd = Path(tmp).resolve()
                executor.execute("run_command", PlannedTask(action="run_command", target="mkdir -p demo"))
                executor.execute("run_command", PlannedTask(action="run_command", target="touch demo/a.txt"))
                self.assertTrue((Path(tmp) / "demo" / "a.txt").exists())
                results = executor.compensate()
                self.assertEqual(len(results), 2)
                self.assertTrue(all(item.success for item in results))
                self.assertFalse((Path(tmp) / "demo").exists())
                self.assertEqual(executor.compensate(), [])
            finally:
                executor.close()

    def test_timeout_auto_undoes_journal(self):
        from unittest.mock import patch

        from auto_system_agent.models import ExecutionResult, PlannedTask
        from auto_system_agent.safe_executor import SafeExecutor
        from auto_system_agent.terminal import TerminalSession

        executor = SafeExecutor()
        try:
            def fake_run(command, timeout=300):
                if command == "sleep 600":
                    return ExecutionResult(
                        success=False,
                        message=f"Command timed out after {timeout}s: {command}",
                        data={"command": command},
                    )
                return ExecutionResult(success=True, message="ok", data={"exit_code": 0})

            with patch.object(TerminalSession, "run", side_effect=fake_run):
                executor.execute("run_command", PlannedTask(action="run_command", target="mkdir -p demo"))
                result = executor.execute("run_command", PlannedTask(action="run_command", target="sleep 600"))
            self.assertFalse(result.success)
            self.assertEqual(result.data.get("compensated"), ["rmdir demo"])
        finally:
            executor.close()


class AbortRunTests(unittest.TestCase):
    def _agent(self, plans, verdicts, executor, log_path):
        from auto_system_agent.agent import AutoSystemAgent
        from auto_system_agent.models import Evaluation

        class ListPlanner:
            def plan_tasks(self, user_input):
                from auto_system_agent.models import PlannedTask

                return [PlannedTask(action="run_command", target=cmd, raw_input=user_input) for cmd in plans]

        class ScriptedSelector:
            SUPPORTED_ACTIONS = {"run_command", "help"}

            def select(self, task):
                return "run_command"

        class ScriptedEvaluator:
            def __init__(self):
                self.calls = 0

            def evaluate_with_llm(self, user_input, task, tool, result, scratchpad):
                verdict = verdicts[min(self.calls, len(verdicts) - 1)]
                self.calls += 1
                return Evaluation(verdict=verdict, reason=f"scripted {verdict}")

        class ScriptedFormatter:
            def format(self, result):
                return result.message

            def format_many(self, results):
                return "\n".join(item.message for item in results)

        class QuietAssistant:
            def resolve(self, *args):
                return None

        from auto_system_agent.event_logger import EventLogger

        return AutoSystemAgent(
            planner=ListPlanner(),
            selector=ScriptedSelector(),
            executor=executor,
            formatter=ScriptedFormatter(),
            assistant=QuietAssistant(),
            event_logger=EventLogger(log_path=log_path),
            evaluator=ScriptedEvaluator(),
            confirm_high_risk=False,
        )

    def test_kill_mid_install_proves_compensating(self):
        import tempfile
        from unittest.mock import patch

        from auto_system_agent.models import ExecutionResult
        from auto_system_agent.safe_executor import SafeExecutor
        from auto_system_agent.terminal import TerminalSession

        with tempfile.TemporaryDirectory() as tmp:
            executor = SafeExecutor()
            try:
                executor.terminal._cwd = Path(tmp).resolve()
                real_run = TerminalSession.run.__get__(executor.terminal)

                def flaky_run(command, timeout=300):
                    if command == "sudo apt install -y vlc":
                        return ExecutionResult(
                            success=False,
                            message=f"Command timed out after {timeout}s: {command}",
                            data={"command": command},
                        )
                    return real_run(command, timeout=timeout)

                with patch.object(TerminalSession, "run", side_effect=flaky_run):
                    agent = self._agent(
                        ["mkdir -p demo", "touch demo/a.txt", "sudo apt install -y vlc"],
                        ["done", "done", "abort"],
                        executor,
                        Path(tmp) / "events.jsonl",
                    )
                    reply = agent.process("set up demo then install vlc")
                trail = agent._last_run_state.trail
                self.assertIn("COMPENSATING", trail)
                self.assertEqual(agent._last_run_state.current, "FAILED")
                self.assertIn("Stopped", reply)
                self.assertFalse((Path(tmp) / "demo").exists())
                compensated = [entry for entry in executor.journal if entry["compensated"]]
                self.assertTrue(compensated, "timeout must undo journaled work")
            finally:
                executor.close()

    def test_failed_exit_compensates_prior_work(self):
        import tempfile

        from auto_system_agent.safe_executor import SafeExecutor

        with tempfile.TemporaryDirectory() as tmp:
            executor = SafeExecutor()
            try:
                executor.terminal._cwd = Path(tmp).resolve()
                agent = self._agent(
                    ["mkdir -p demo", "false"], ["done", "abort"], executor, Path(tmp) / "events.jsonl"
                )
                reply = agent.process("make demo then fail")
                self.assertIn("COMPENSATING", agent._last_run_state.trail)
                self.assertIn("Stopped", reply)
                self.assertIn("Compensated 1 action(s): rmdir demo", reply)
                self.assertFalse((Path(tmp) / "demo").exists())
            finally:
                executor.close()

    def test_missing_binary_blocks_without_compensation(self):
        import tempfile

        from auto_system_agent.safe_executor import SafeExecutor

        with tempfile.TemporaryDirectory() as tmp:
            executor = SafeExecutor()
            try:
                agent = self._agent(
                    ["definitely-not-a-real-binary-xyz"],
                    ["abort"],
                    executor,
                    Path(tmp) / "events.jsonl",
                )
                reply = agent.process("run missing thing")
                self.assertIn("COMPENSATING", agent._last_run_state.trail)
                self.assertNotIn("Compensated", reply)
                self.assertIn("Stopped", reply)
            finally:
                executor.close()

    def test_retry_limit_escalates_to_replan(self):
        from auto_system_agent.evaluator import Evaluator
        from auto_system_agent.models import ExecutionResult, PlannedTask, ReActStep

        evaluator = Evaluator()
        failed = ExecutionResult(success=False, message="Command failed with code 1.")
        scratchpad = [
            ReActStep(thought="t", task=PlannedTask(action="run_command", target="ls /nope"), tool="run_command", result=failed),
            ReActStep(thought="t", task=PlannedTask(action="run_command", target="ls /nope"), tool="run_command", result=failed),
        ]
        verdict = evaluator.evaluate(
            "list nope", PlannedTask(action="run_command", target="ls /nope"), "run_command", failed, scratchpad
        )
        self.assertEqual(verdict.verdict, "replan")

    def test_bounded_queue_applies_back_pressure(self):
        import threading
        import time

        from auto_system_agent.models import ExecutionResult, PlannedTask
        from auto_system_agent.safe_executor import SafeExecutor

        executor = SafeExecutor(max_workers=1, queue_size=2)
        try:
            started = threading.Event()
            original = executor.execute

            def slow_execute(tool_key, task):
                started.set()
                time.sleep(0.5)
                return ExecutionResult(success=True, message="ok")

            executor.execute = slow_execute
            first = executor.submit("run_command", PlannedTask(action="run_command", target="a"))
            self.assertTrue(started.wait(2))
            second = executor.submit("run_command", PlannedTask(action="run_command", target="b"))
            self.assertTrue(executor.queue_full())
            self.assertEqual([f.result(5).message for f in (first, second)], ["ok", "ok"])
            self.assertFalse(executor.queue_full())
        finally:
            executor.execute = original
            executor.close()


if __name__ == "__main__":
    unittest.main()
