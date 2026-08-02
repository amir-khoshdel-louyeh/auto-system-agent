"""Phase 6 gate: planner output takes the hand-authored workflow path.

A planned plan is compiled to DSL text, parsed by the loader, validated by
the five-stage validator and gated by the same policy service as any other
node — no plan can skip validation or policy, and an invalid plan is
rejected as a whole.
"""

import unittest

from engine.executor.scheduler import compute_levels
from orchestration.offline_planner import OfflinePlanner
from orchestration.plan_compiler import (
    PlanRejected,
    compile_plan,
    plan_policy_verdicts,
    policy_verdicts,
)


class WorkspacePlanTests(unittest.TestCase):
    def setUp(self):
        self.planner = OfflinePlanner()

    def test_workspace_plan_compiles_and_validates(self):
        plan = self.planner.plan(
            "create a workspace called demo-app with three folders and a README"
        )
        self.assertEqual(plan.intent, "create_workspace")
        workflow = compile_plan(plan, name="onboarding")
        self.assertEqual(workflow.name, "onboarding")
        self.assertEqual(workflow.version, 1)
        ids = [node.id for node in workflow.nodes]
        self.assertEqual(ids[0], "create-demo-app")
        self.assertIn("write-readme", ids)

    def test_independent_folder_nodes_run_in_parallel(self):
        plan = self.planner.plan(
            "create a workspace called demo-app with three folders and a README"
        )
        workflow = compile_plan(plan)
        levels = compute_levels({node.id: node for node in workflow.nodes}, list(workflow.edges))
        self.assertEqual(levels[0], ["create-demo-app"])
        self.assertEqual(len(levels[1]), 4, "three folders + README run concurrently")

    def test_every_node_is_policy_decided(self):
        plan = self.planner.plan("create a workspace called demo-app")
        verdicts = plan_policy_verdicts(plan)
        self.assertEqual(set(verdicts), {node.id for node in compile_plan(plan).nodes})
        self.assertEqual(set(verdicts.values()), {"ALLOW"})

    def test_default_folders_are_used_when_none_are_named(self):
        plan = self.planner.plan("set up the alpha workspace")
        ids = [step.id for step in plan.steps]
        self.assertEqual(
            ids, ["create-alpha", "mkdir-logs", "mkdir-reports", "mkdir-backups", "write-readme"]
        )

    def test_folder_request_creates_one_parallel_node_each(self):
        plan = self.planner.plan("create three folders called reports, logs and backups")
        workflow = compile_plan(plan)
        levels = compute_levels({node.id: node for node in workflow.nodes}, list(workflow.edges))
        self.assertEqual(len(levels), 1)
        self.assertEqual(len(levels[0]), 3)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.planner = OfflinePlanner()

    def test_install_plan_gates_the_shell_node_behind_an_approval(self):
        plan = self.planner.plan("install firefox")
        workflow = compile_plan(plan)
        approval = [node for node in workflow.nodes if node.type == "approval"]
        shell = [node for node in workflow.nodes if node.type == "shell"]
        self.assertEqual(len(approval), 1)
        self.assertEqual(approval[0].params["quorum"], 1)
        self.assertEqual(list(workflow.edges), [(approval[0].id, shell[0].id)])
        self.assertTrue(shell[0].params["run"].endswith("firefox"))

    def test_statically_dangerous_command_is_rejected_before_deploy(self):
        plan = self.planner.plan("run rm -rf /")
        with self.assertRaises(PlanRejected) as ctx:
            compile_plan(plan)
        self.assertEqual(ctx.exception.errors[0].code, "policy")

    def test_runtime_deny_still_applies_to_a_validatable_command(self):
        # `rm -f /etc/passwd` passes the static danger list but the policy
        # service denies it at the gate: planning never bypasses policy.
        plan = self.planner.plan("run rm -f /etc/passwd")
        verdicts = plan_policy_verdicts(plan)
        self.assertEqual(set(verdicts.values()), {"DENY"})

    def test_policy_verdicts_include_need_approval_for_gated_types(self):
        from engine.dsl.loader import load_text

        workflow = load_text(
            '{"workflow": "gated", "version": 1, "nodes": '
            '[{"id": "c", "type": "custom", "func": "plugins.run"}], "edges": []}',
            "json",
        )
        self.assertEqual(policy_verdicts(workflow), {"c": "NEED_APPROVAL"})

    def test_write_and_read_plans_validate(self):
        for text in (
            "write a README with the text hello team",
            "list the files in ~/Downloads",
            "how much disk space is free",
            "run ls -la",
        ):
            with self.subTest(text=text):
                plan = self.planner.plan(text)
                workflow = compile_plan(plan)
                self.assertEqual(len(workflow.nodes), 1)
                self.assertEqual(set(policy_verdicts(workflow).values()), {"ALLOW"})

    def test_incomplete_plan_is_refused_rather_than_guessed(self):
        plan = self.planner.plan("install")
        self.assertEqual(plan.intent, "install_app")
        self.assertFalse(plan.executable)
        with self.assertRaises(ValueError):
            compile_plan(plan)

    def test_unsupported_request_cannot_compile(self):
        plan = self.planner.plan("tell me a joke")
        self.assertEqual(plan.intent, "unknown")
        with self.assertRaises(ValueError):
            compile_plan(plan)
        self.assertFalse(plan.executable)

    def test_rejected_plan_reports_the_first_validation_error(self):
        # A plan whose node would fail validation (empty command) must be
        # rejected as a whole, with the stage carried in the exception.
        from orchestration.intents import Plan, PlanStep

        broken = Plan(
            intent="run_command",
            slots={"command": "ls"},
            steps=(PlanStep(id="run-command", type="shell", params={"run": ""}),),
        )
        with self.assertRaises(PlanRejected) as ctx:
            compile_plan(broken)
        self.assertEqual(ctx.exception.errors[0].code, "type")

    def test_plan_document_is_json_serialisable(self):
        import json

        plan = self.planner.plan("create a workspace called demo")
        self.assertIsInstance(json.dumps(plan.to_document("d")), str)


if __name__ == "__main__":
    unittest.main()
