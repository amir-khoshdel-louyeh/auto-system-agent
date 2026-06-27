"""P3.5: FSM transition coverage, chain hashing, timeouts, compensation."""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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


if __name__ == "__main__":
    unittest.main()
