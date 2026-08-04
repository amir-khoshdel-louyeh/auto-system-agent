"""Phase 8 item [78]: human-readable policy explanations.

An explanation never changes the verdict: it restates the policy service's
decision as a headline, the reasons behind it and the next action, so the
same decision always renders the same words for the API and the client.
"""

import unittest

from safety.explanations import BANDS, explain, explain_command, risk_band
from safety.policy_service import ALLOW, DENY, NEED_APPROVAL, PolicyDecision, PolicyService


def decision(verdict: str, *, score: int = 0, reasons=(), quorum: int = 0) -> PolicyDecision:
    return PolicyDecision(
        node_id="n1",
        node_type="shell",
        verdict=verdict,
        score=score,
        reasons=tuple(reasons),
        quorum=quorum,
        policy_version="policy-1",
    )


class RiskBandTests(unittest.TestCase):
    def test_bands_are_ordered_and_cover_the_range(self):
        self.assertEqual(BANDS[0][0], 30)
        self.assertEqual(risk_band(0), "low")
        self.assertEqual(risk_band(30), "low")
        self.assertEqual(risk_band(31), "high")
        self.assertEqual(risk_band(100), "high")


class ExplanationTests(unittest.TestCase):
    def test_allowed_node_reads_as_actionable(self):
        text = explain(decision(ALLOW, score=10, reasons=("command is read-only",)))
        self.assertIn("Allowed", text.headline)
        self.assertIn("low risk", text.headline)
        self.assertIn("command is read-only", text.detail)
        self.assertIn("No action needed", text.next_step)

    def test_allowed_node_without_reasons_says_so(self):
        self.assertIn("No risk signals", explain(decision(ALLOW)).detail)

    def test_approval_states_the_quorum(self):
        single = explain(decision(NEED_APPROVAL, score=70, reasons=("sudo install",), quorum=1))
        self.assertIn("Needs approval", single.headline)
        self.assertIn("one approver", single.headline)
        double = explain(decision(NEED_APPROVAL, score=70, quorum=2))
        self.assertIn("2 approvers", double.headline)
        self.assertIn("2 approvers", double.next_step)

    def test_denied_explains_that_it_cannot_be_approved(self):
        text = explain(decision(DENY, score=100, reasons=("destructive command",)))
        self.assertIn("Denied", text.headline)
        self.assertIn("destructive command", text.detail)
        self.assertIn("cannot be approved", text.next_step)

    def test_multiple_reasons_are_joined(self):
        text = explain(decision(DENY, reasons=("one", "two", "three")))
        self.assertEqual(text.detail, "Reasons: one; two; three.")

    def test_dict_input_renders_the_same_text(self):
        raw = decision(NEED_APPROVAL, score=65, reasons=("x",), quorum=1).as_dict()
        self.assertEqual(
            explain(raw), explain(decision(NEED_APPROVAL, score=65, reasons=("x",), quorum=1))
        )

    def test_serialization_has_the_frozen_keys(self):
        self.assertEqual(
            set(explain(decision(ALLOW)).as_dict()),
            {
                "node_id",
                "verdict",
                "headline",
                "detail",
                "next_step",
                "score",
                "quorum",
                "policy_version",
            },
        )

    def test_explanation_never_invents_a_verdict(self):
        for verdict in (ALLOW, NEED_APPROVAL, DENY):
            with self.subTest(verdict=verdict):
                self.assertEqual(explain(decision(verdict)).verdict, verdict)


class CommandExplanationTests(unittest.TestCase):
    def test_read_only_command_is_allowed(self):
        text = explain_command("ls -la")
        self.assertEqual(text.verdict, ALLOW)
        self.assertIn("Allowed", text.headline)

    def test_destructive_command_is_denied(self):
        text = explain_command("rm -rf /")
        self.assertEqual(text.verdict, DENY)
        self.assertIn("Denied", text.headline)

    def test_root_role_cannot_bypass_the_denial(self):
        self.assertEqual(explain_command("rm -rf /", role="OWNER").verdict, DENY)

    def test_explanation_matches_the_service_decision(self):
        service = PolicyService()
        for command in ("ls", "sudo apt install -y vlc", "rm -rf /"):
            with self.subTest(command=command):
                self.assertEqual(
                    explain_command(command).verdict,
                    service.decide("", {"type": "shell", "run": command}).verdict,
                )


if __name__ == "__main__":
    unittest.main()
