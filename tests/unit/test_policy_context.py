"""Phase 3 item [34]: workspace, role and path boundary checks (FR-02, FR-07).

With a PolicyContext the service denies callers whose role may not execute
nodes and shell commands that reach outside the approved workspace root
(absolute paths and `..` traversal), while in-workspace paths pass.
"""

import unittest

from safety.policy_service import ALLOW, DENY, PolicyContext, PolicyService

SERVICE = PolicyService()


class RoleCheckTests(unittest.TestCase):
    def test_viewer_may_not_execute(self):
        decision = SERVICE.decide(
            "a", {"type": "shell", "run": "echo hi"}, context=PolicyContext(role="VIEWER")
        )
        self.assertEqual(decision.verdict, DENY)
        self.assertTrue(any("role" in reason for reason in decision.reasons))

    def test_operator_and_owner_may_execute(self):
        for role in ("OPERATOR", "OWNER"):
            with self.subTest(role=role):
                decision = SERVICE.decide(
                    "a", {"type": "shell", "run": "echo hi"}, context=PolicyContext(role=role)
                )
                self.assertEqual(decision.verdict, ALLOW)


class WorkspacePathTests(unittest.TestCase):
    def setUp(self):
        self.ctx = PolicyContext(role="OPERATOR", workspace_root="/workspace")

    def test_in_workspace_absolute_path_passes(self):
        decision = SERVICE.decide(
            "a", {"type": "shell", "run": "ls /workspace/data"}, context=self.ctx
        )
        self.assertEqual(decision.verdict, ALLOW)

    def test_absolute_path_outside_workspace_is_denied(self):
        decision = SERVICE.decide(
            "a", {"type": "shell", "run": "cat /etc/passwd"}, context=self.ctx
        )
        self.assertEqual(decision.verdict, DENY)
        self.assertTrue(any("outside the workspace" in reason for reason in decision.reasons))

    def test_parent_traversal_is_denied(self):
        decision = SERVICE.decide(
            "a", {"type": "shell", "run": "rm -rf ../outside"}, context=self.ctx
        )
        self.assertEqual(decision.verdict, DENY)
        self.assertTrue(any("traversal" in reason for reason in decision.reasons))

    def test_relative_path_inside_workspace_passes(self):
        decision = SERVICE.decide("a", {"type": "shell", "run": "ls ./data"}, context=self.ctx)
        self.assertEqual(decision.verdict, ALLOW)

    def test_tilde_path_outside_workspace_is_denied(self):
        decision = SERVICE.decide("a", {"type": "shell", "run": "ls ~/secrets"}, context=self.ctx)
        self.assertEqual(decision.verdict, DENY)


class ContextOptionalTests(unittest.TestCase):
    def test_no_context_keeps_prior_behaviour(self):
        self.assertEqual(SERVICE.decide("a", {"type": "shell", "run": "echo hi"}).verdict, ALLOW)
        self.assertEqual(SERVICE.decide("a", {"type": "shell", "run": "rm -rf /"}).verdict, DENY)


if __name__ == "__main__":
    unittest.main()
