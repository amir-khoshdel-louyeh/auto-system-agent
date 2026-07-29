"""Phase 3 item [29]: READY/RUNNING/SUCCEEDED/FAILED/BLOCKED state machine.

The vocabulary and transitions the durable run model shares with the
scheduler: legal lifecycle paths pass, terminal states never leave, and the
executor's node status maps onto the stored step states.
"""

import unittest

from engine.executor import states as S


class TransitionTests(unittest.TestCase):
    def test_happy_path(self):
        for src, dst in [
            (S.PENDING, S.READY),
            (S.READY, S.RUNNING),
            (S.RUNNING, S.SUCCEEDED),
        ]:
            with self.subTest(src=src, dst=dst):
                self.assertTrue(S.can_transition(src, dst))
                S.require_transition(src, dst)  # does not raise

    def test_retry_loop(self):
        self.assertTrue(S.can_transition(S.RUNNING, S.RETRYING))
        self.assertTrue(S.can_transition(S.RETRYING, S.RUNNING))

    def test_failure_then_compensation(self):
        self.assertTrue(S.can_transition(S.RUNNING, S.FAILED))
        self.assertTrue(S.can_transition(S.FAILED, S.COMPENSATING))
        self.assertTrue(S.can_transition(S.COMPENSATING, S.COMPENSATED))

    def test_blocked_is_reachable_from_pending_or_ready(self):
        self.assertTrue(S.can_transition(S.PENDING, S.BLOCKED))
        self.assertTrue(S.can_transition(S.READY, S.BLOCKED))

    def test_illegal_transitions_are_rejected(self):
        for src, dst in [
            (S.PENDING, S.RUNNING),  # must pass through READY
            (S.SUCCEEDED, S.RUNNING),  # terminal
            (S.BLOCKED, S.RUNNING),  # terminal
            (S.RUNNING, S.READY),  # no going back
            (S.COMPENSATED, S.RUNNING),  # terminal
        ]:
            with self.subTest(src=src, dst=dst):
                self.assertFalse(S.can_transition(src, dst))
                with self.assertRaises(S.InvalidTransition):
                    S.require_transition(src, dst)

    def test_terminal_states(self):
        for state in (S.SUCCEEDED, S.FAILED, S.BLOCKED, S.COMPENSATED):
            self.assertTrue(S.is_terminal(state))
        for state in (S.PENDING, S.READY, S.RUNNING, S.RETRYING, S.COMPENSATING):
            self.assertFalse(S.is_terminal(state))

    def test_every_transition_target_is_a_known_state(self):
        for src, targets in S.ALLOWED.items():
            self.assertIn(src, S.ALL_STATES)
            for dst in targets:
                self.assertIn(dst, S.ALL_STATES)


class StateForTests(unittest.TestCase):
    def test_executor_status_maps_to_run_state(self):
        self.assertEqual(S.state_for("OK"), S.SUCCEEDED)
        self.assertEqual(S.state_for("FAILED"), S.FAILED)
        self.assertEqual(S.state_for("SKIPPED"), S.BLOCKED)
        self.assertEqual(S.state_for("CANCELLED"), S.FAILED)

    def test_unknown_status_fails_closed_to_failed(self):
        self.assertEqual(S.state_for("weird"), S.FAILED)


if __name__ == "__main__":
    unittest.main()
