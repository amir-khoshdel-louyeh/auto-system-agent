import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.planner import Planner


class PlannerVariedPhrasingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = Planner()

    def test_install_phrasings(self) -> None:
        cases = [
            ("install vlc", "vlc"),
            ("please install app firefox", "firefox"),
            ("can you install google chrome", "google chrome"),
        ]
        for text, expected_target in cases:
            with self.subTest(text=text):
                task = self.planner.plan(text)
                self.assertEqual(task.action, "install_app")
                self.assertEqual(task.target, expected_target)

    def test_create_folder_phrasings(self) -> None:
        cases = [
            "create folder demo",
            "create directory demo",
            "make folder demo",
            "please make directory demo",
        ]
        for text in cases:
            with self.subTest(text=text):
                task = self.planner.plan(text)
                self.assertEqual(task.action, "create_folder")
                self.assertEqual(task.target, "demo")

    def test_compress_phrasings(self) -> None:
        cases = ["compress project", "zip project", "archive project"]
        for text in cases:
            with self.subTest(text=text):
                task = self.planner.plan(text)
                self.assertEqual(task.action, "compress")
                self.assertEqual(task.target, "project")

    def test_list_files_phrasings(self) -> None:
        task = self.planner.plan("show files in downloads")
        self.assertEqual(task.action, "list_files")
        self.assertEqual(task.target, "downloads")

        task = self.planner.plan("list directory")
        self.assertEqual(task.action, "list_files")
        self.assertEqual(task.target, ".")

    def test_move_and_rename_phrasings(self) -> None:
        task = self.planner.plan("move a.txt to b.txt")
        self.assertEqual(task.action, "move_path")
        self.assertEqual(task.target, "a.txt")
        self.assertEqual(task.options["destination"], "b.txt")

        task = self.planner.plan("rename old.txt to new.txt")
        self.assertEqual(task.action, "move_path")
        self.assertEqual(task.target, "old.txt")
        self.assertEqual(task.options["destination"], "new.txt")

    def test_delete_and_remove_phrasings(self) -> None:
        cases = ["delete temp", "delete folder temp", "remove file temp"]
        for text in cases:
            with self.subTest(text=text):
                task = self.planner.plan(text)
                self.assertEqual(task.action, "delete_path")
                self.assertEqual(task.target, "temp")

    def test_execute_phrasing_maps_to_run_command(self) -> None:
        task = self.planner.plan("execute pwd")
        self.assertEqual(task.action, "run_command")
        self.assertEqual(task.target, "pwd")

    def test_cd_prefix_maps_to_run_command(self) -> None:
        task = self.planner.plan("cd ..")
        self.assertEqual(task.action, "run_command")
        self.assertEqual(task.target, "cd ..")

    def test_help_keyword_maps_to_help_action(self) -> None:
        task = self.planner.plan("help")
        self.assertEqual(task.action, "help")

    def test_plan_tasks_splits_multi_step_instruction(self) -> None:
        tasks = self.planner.plan_tasks("create folder demo then list files in demo")

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].action, "create_folder")
        self.assertEqual(tasks[0].target, "demo")
        self.assertEqual(tasks[1].action, "list_files")
        self.assertEqual(tasks[1].target, "demo")

    def test_invalid_move_fragment_falls_back_to_unknown(self) -> None:
        task = self.planner.plan("move")
        self.assertEqual(task.action, "unknown")

    def test_install_aliases_are_normalized(self) -> None:
        task = self.planner.plan("install chrome")
        self.assertEqual(task.action, "install_app")
        self.assertEqual(task.target, "google chrome")

    def test_paths_and_quotes_are_normalized(self) -> None:
        task = self.planner.plan("create folder '~/demo folder'")
        self.assertEqual(task.action, "create_folder")
        self.assertEqual(task.target, str(Path("~/demo folder").expanduser()))

    def test_multi_step_includes_dependencies_and_rollback_hints(self) -> None:
        tasks = self.planner.plan_tasks("create folder demo then move demo to archive/demo")
        self.assertEqual(tasks[0].options.get("depends_on_steps"), [])
        self.assertIn("delete_path", str(tasks[0].options.get("rollback_hint", "")))
        self.assertEqual(tasks[1].options.get("depends_on_steps"), [1])
        self.assertIn("move_path", str(tasks[1].options.get("rollback_hint", "")))


if __name__ == "__main__":
    unittest.main()
