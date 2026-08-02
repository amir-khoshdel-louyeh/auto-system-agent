"""Phase 6 item [59]: confidence threshold and clarification responses.

The planner never guesses: a request below the confidence threshold, missing
a required slot, or outside the vocabulary comes back with an actionable
question (or the capability list) and is not executable, so nothing
downstream can run a half-understood plan.
"""

import unittest

from orchestration.offline_planner import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    OfflinePlanner,
    PlannerConfig,
    capability_text,
    clarification_for,
    is_help,
)


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.planner = OfflinePlanner()

    def test_default_threshold_is_configuration(self):
        self.assertEqual(DEFAULT_CONFIDENCE_THRESHOLD, 0.75)
        self.assertEqual(PlannerConfig().confidence_threshold, 0.75)

    def test_confident_plan_is_executable_without_clarification(self):
        plan = self.planner.plan("install firefox")
        self.assertFalse(plan.needs_clarification)
        self.assertTrue(plan.executable)
        self.assertEqual(plan.clarification, "")

    def test_low_confidence_rule_asks_for_a_rephrase(self):
        quiet = OfflinePlanner(PlannerConfig(confidence_threshold=0.95))
        plan = quiet.plan("install firefox")  # rule confidence 0.90
        self.assertTrue(plan.needs_clarification)
        self.assertIn("not confident", plan.clarification)
        self.assertIn("install_app", plan.clarification)
        self.assertFalse(plan.executable)

    def test_raising_the_threshold_never_invents_steps(self):
        quiet = OfflinePlanner(PlannerConfig(confidence_threshold=1.0))
        for text in ("install vlc", "mkdir logs", "run ls -la"):
            with self.subTest(text=text):
                plan = quiet.plan(text)
                self.assertTrue(plan.needs_clarification)
                self.assertFalse(plan.executable)


class MissingSlotTests(unittest.TestCase):
    def setUp(self):
        self.planner = OfflinePlanner()

    def test_missing_slot_asks_a_specific_question(self):
        plan = self.planner.plan("install")
        self.assertEqual(plan.intent, "install_app")
        self.assertTrue(plan.needs_clarification)
        self.assertEqual(plan.clarification, "Which application should I install?")
        self.assertFalse(plan.executable)

    def test_directory_question_is_intent_aware(self):
        base = clarification_for("path", "path")
        self.assertEqual(
            clarification_for("list_directory", "path"), "Which directory should I list?"
        )
        self.assertEqual(clarification_for("write_file", "path"), "Which file should I write?")
        self.assertEqual(clarification_for("create_folder", "folder_names"), SLOT_FOLDER)
        self.assertNotEqual(base, "")

    def test_clarified_plan_cannot_be_compiled(self):
        from orchestration.plan_compiler import compile_plan

        plan = self.planner.plan("install")
        with self.assertRaises(ValueError):
            compile_plan(plan)

    def test_partial_slots_are_still_reported(self):
        plan = self.planner.plan("write a file named notes.md")
        self.assertTrue(plan.needs_clarification)
        self.assertEqual(plan.slots.get("path"), "notes.md")
        self.assertIn("text", plan.clarification)


class HelpAndUnsupportedTests(unittest.TestCase):
    def setUp(self):
        self.planner = OfflinePlanner()

    def test_help_returns_the_capability_list(self):
        plan = self.planner.plan("what can you do")
        self.assertEqual(plan.intent, "help")
        self.assertFalse(plan.needs_clarification)
        self.assertFalse(plan.executable)
        self.assertIn("install_app", plan.message)
        self.assertTrue(is_help("help"))

    def test_unsupported_request_asks_what_to_do(self):
        plan = self.planner.plan("tell me a joke")
        self.assertEqual(plan.intent, "unknown")
        self.assertTrue(plan.needs_clarification)
        self.assertFalse(plan.executable)
        self.assertIn("could not map", plan.clarification)
        self.assertIn("create_workspace", plan.message)

    def test_empty_request_asks_what_to_do(self):
        plan = self.planner.plan("   ")
        self.assertTrue(plan.needs_clarification)
        self.assertFalse(plan.executable)

    def test_capability_text_lists_only_executable_intents(self):
        text = capability_text()
        self.assertIn("create_folder", text)
        self.assertNotIn("- unknown", text)


class StepLimitTests(unittest.TestCase):
    def test_oversized_plan_asks_for_a_smaller_request(self):
        planner = OfflinePlanner(PlannerConfig(max_steps=2))
        plan = planner.plan("create three folders called reports, logs and backups")
        self.assertTrue(plan.needs_clarification)
        self.assertIn("split it", plan.clarification)
        self.assertFalse(plan.executable)


SLOT_FOLDER = "What should the folder(s) be called?"


if __name__ == "__main__":
    unittest.main()
