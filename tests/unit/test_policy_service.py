"""Phase 3 item [33]: dedicated policy service (FR-07, decision boundary).

Every executable node gets exactly one decision — ALLOW / NEED_APPROVAL /
DENY — carrying a risk score, reasons, required quorum and policy version.
The executor gate must agree with the service (it is a thin adapter).
"""

import asyncio
import unittest

from engine.executor.gate import decide as gate_decide
from safety.policy_service import (
    ALLOW,
    DENY,
    NEED_APPROVAL,
    PolicyDecision,
    PolicyService,
    looks_obfuscated,
)

SERVICE = PolicyService()


class ShellDecisionTests(unittest.TestCase):
    def test_safe_command_is_allowed(self):
        decision = SERVICE.decide("a", {"type": "shell", "run": "echo hello"})
        self.assertEqual(decision.verdict, ALLOW)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.quorum, 0)
        self.assertLessEqual(decision.score, 30)

    def test_destructive_command_is_denied(self):
        decision = SERVICE.decide("a", {"type": "shell", "run": "rm -rf /"})
        self.assertEqual(decision.verdict, DENY)
        self.assertTrue(decision.denied)
        self.assertTrue(decision.reasons)

    def test_medium_risk_command_needs_one_approval(self):
        decision = SERVICE.decide("a", {"type": "shell", "run": "rm -rf /tmp/project"})
        self.assertEqual(decision.verdict, NEED_APPROVAL)
        self.assertEqual(decision.quorum, 1)

    def test_obfuscated_payload_escalates_a_safe_command(self):
        payload = "echo " + "A" * 250
        self.assertTrue(looks_obfuscated(payload))
        decision = SERVICE.decide("a", {"type": "shell", "run": payload})
        self.assertEqual(decision.verdict, NEED_APPROVAL)
        self.assertEqual(decision.quorum, 1)


class NodeTypeDecisionTests(unittest.TestCase):
    def test_approval_node_is_allowed(self):
        decision = SERVICE.decide("gate", {"type": "approval", "quorum": 2})
        self.assertEqual(decision.verdict, ALLOW)

    def test_gui_node_needs_one_approval(self):
        decision = SERVICE.decide("g", {"type": "gui_click", "target": "x"})
        self.assertEqual(decision.verdict, NEED_APPROVAL)
        self.assertEqual(decision.quorum, 1)

    def test_custom_node_needs_two_person_approval(self):
        decision = SERVICE.decide("c", {"type": "custom", "func": "pkg.run"})
        self.assertEqual(decision.verdict, NEED_APPROVAL)
        self.assertEqual(decision.quorum, 2)

    def test_unknown_node_type_fails_closed(self):
        decision = SERVICE.decide("x", {"type": "robot_arm"})
        self.assertEqual(decision.verdict, DENY)

    def test_missing_node_type_fails_closed(self):
        self.assertEqual(SERVICE.decide("x", {}).verdict, DENY)


class DecisionShapeTests(unittest.TestCase):
    def test_decision_carries_score_reasons_and_policy_version(self):
        decision = SERVICE.decide("a", {"type": "shell", "run": "echo hi"})
        self.assertIsInstance(decision, PolicyDecision)
        self.assertEqual(decision.policy_version, "policy-1")
        self.assertIsInstance(decision.reasons, tuple)
        self.assertGreaterEqual(decision.score, 0)
        self.assertLessEqual(decision.score, 100)

    def test_as_dict_is_audit_ready(self):
        payload = SERVICE.decide("a", {"type": "shell", "run": "rm -rf /"}).as_dict()
        self.assertEqual(
            set(payload),
            {"node_id", "node_type", "verdict", "score", "reasons", "quorum", "policy_version"},
        )
        self.assertEqual(payload["node_id"], "a")

    def test_gate_adapter_agrees_with_the_service(self):
        specs = [
            {"type": "shell", "run": "echo hi"},
            {"type": "shell", "run": "rm -rf /"},
            {"type": "gui_click", "target": "x"},
            {"type": "approval", "quorum": 1},
        ]
        for spec in specs:
            with self.subTest(spec=spec):
                expected = SERVICE.decide("n", spec).verdict
                self.assertEqual(asyncio.run(gate_decide("n", spec)), expected)


if __name__ == "__main__":
    unittest.main()
