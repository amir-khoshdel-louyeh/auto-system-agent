"""Phase 6 item [55]: frozen intents, slots and the canonical plan schema.

The vocabulary is project-owned data: every intent declares the slots it
needs, the executable intents are a subset, and a plan emits a DSL document
the existing loader/validator already understands.
"""

import unittest

from orchestration.intents import (
    EXECUTABLE_INTENTS,
    INTENT_SPECS,
    INTENTS,
    METRIC_COMMANDS,
    SLOT_TYPES,
    SYSTEM_METRICS,
    Plan,
    PlanStep,
    missing_slots,
    spec_for,
)


class VocabularyTests(unittest.TestCase):
    def test_intent_vocabulary_is_frozen(self):
        self.assertEqual(
            INTENTS,
            (
                "create_folder",
                "write_file",
                "create_workspace",
                "install_app",
                "run_command",
                "system_info",
                "list_directory",
                "help",
                "unknown",
            ),
        )
        self.assertEqual(set(INTENT_SPECS), set(INTENTS))

    def test_executable_intents_are_a_subset(self):
        self.assertTrue(set(EXECUTABLE_INTENTS) <= set(INTENTS))
        self.assertNotIn("help", EXECUTABLE_INTENTS)
        self.assertNotIn("unknown", EXECUTABLE_INTENTS)

    def test_every_declared_slot_has_a_type(self):
        declared = {
            slot
            for spec in INTENT_SPECS.values()
            for slot in (*spec.required_slots, *spec.optional_slots)
        }
        self.assertTrue(declared <= set(SLOT_TYPES), declared - set(SLOT_TYPES))

    def test_every_intent_has_examples(self):
        for name, spec in INTENT_SPECS.items():
            with self.subTest(intent=name):
                self.assertTrue(spec.examples, f"{name} needs at least one example")

    def test_system_metrics_have_commands(self):
        self.assertEqual(set(SYSTEM_METRICS), set(METRIC_COMMANDS))

    def test_spec_for_unknown_intent_falls_back(self):
        self.assertEqual(spec_for("teleport").name, "unknown")
        self.assertEqual(spec_for("install_app").node_type, "shell")

    def test_install_is_the_only_intent_requiring_approval(self):
        gated = {name for name, spec in INTENT_SPECS.items() if spec.requires_approval}
        self.assertEqual(gated, {"install_app"})


class MissingSlotTests(unittest.TestCase):
    def test_missing_required_slots_in_declaration_order(self):
        self.assertEqual(missing_slots("write_file", {}), ("path", "content"))
        self.assertEqual(missing_slots("write_file", {"path": "a.md"}), ("content",))

    def test_blank_values_count_as_missing(self):
        self.assertEqual(missing_slots("install_app", {"app": "   "}), ("app",))
        self.assertEqual(missing_slots("create_folder", {"folder_names": []}), ("folder_names",))

    def test_help_needs_nothing(self):
        self.assertEqual(missing_slots("help", {}), ())


class PlanDocumentTests(unittest.TestCase):
    def _plan(self) -> Plan:
        return Plan(
            intent="create_workspace",
            slots={"workspace_name": "demo-app"},
            steps=(
                PlanStep(id="folders", type="shell", params={"run": "mkdir -p demo-app/logs"}),
                PlanStep(
                    id="readme",
                    type="shell",
                    params={"run": "touch demo-app/README.md"},
                    depends_on=("folders",),
                    compensation="rm -f demo-app/README.md",
                ),
            ),
            confidence=0.9,
            raw_input="create the demo-app workspace",
        )

    def test_executable_plan_emits_a_dsl_document(self):
        document = self._plan().to_document("planned-demo")
        self.assertEqual(document["workflow"], "planned-demo")
        self.assertEqual(document["version"], 1)
        self.assertEqual(document["triggers"], [{"manual": True}])
        self.assertEqual([node["id"] for node in document["nodes"]], ["folders", "readme"])
        self.assertEqual(document["edges"], [["folders", "readme"]])
        self.assertEqual(document["policy"], {})

    def test_node_carries_params_and_compensation(self):
        nodes = {node["id"]: node for node in self._plan().to_document("d")["nodes"]}
        self.assertEqual(nodes["readme"]["run"], "touch demo-app/README.md")
        self.assertEqual(nodes["readme"]["compensation"], "rm -f demo-app/README.md")
        self.assertNotIn("compensation", nodes["folders"])

    def test_non_executable_plans_refuse_to_compile(self):
        for plan in (
            Plan(intent="help", planner="offline"),
            Plan(intent="unknown", planner="offline"),
            Plan(intent="create_folder", steps=()),
            Plan(
                intent="install_app",
                steps=(PlanStep(id="a", type="shell"),),
                needs_clarification=True,
            ),
        ):
            with self.subTest(intent=plan.intent):
                self.assertFalse(plan.executable)
                with self.assertRaises(ValueError):
                    plan.to_document("nope")


if __name__ == "__main__":
    unittest.main()
