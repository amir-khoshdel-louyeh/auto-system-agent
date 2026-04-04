import sys
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch
import zipfile
import subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.models import ExecutionResult, PlannedTask
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tools.command_tool import run_command
from auto_system_agent.tools.file_tool import compress_path, create_folder, delete_path


class ExecutionSafetyTests(unittest.TestCase):
    def test_run_command_handles_invalid_shell_syntax(self):
        result = run_command('echo "unterminated')

        self.assertFalse(result.success)
        self.assertIn("Invalid command syntax", result.message)

    def test_run_command_handles_missing_executable(self):
        result = run_command("definitely-not-a-real-binary-xyz")

        self.assertFalse(result.success)
        self.assertIn("Command not found", result.message)

    def test_run_command_blocks_shell_interpreters(self):
        result = run_command("python3 --version")

        self.assertFalse(result.success)
        self.assertIn("blocked by safety policy", result.message)
        self.assertEqual(result.data.get("policy_decision"), "blocked")
        self.assertEqual(result.data.get("policy_reason"), "interpreter_execution")
        self.assertIn(result.data.get("risk_level"), {"low", "medium", "high"})

    def test_run_command_blocks_risky_flags(self):
        result = run_command("echo -rf")

        self.assertFalse(result.success)
        self.assertIn("blocked by safety policy", result.message)
        self.assertEqual(result.data.get("policy_decision"), "blocked")

    def test_run_command_includes_risk_when_allowed(self):
        result = run_command("echo hello")

        self.assertTrue(result.success)
        self.assertEqual(result.data.get("policy_decision"), "approved")
        self.assertEqual(result.data.get("policy_reason"), "allowed_command")
        self.assertIn(result.data.get("risk_level"), {"low", "medium", "high"})

    def test_install_action_handles_missing_package_manager(self):
        executor = SafeExecutor()
        task = PlannedTask(action="install_app", target="vlc", raw_input="install vlc")

        with patch(
            "auto_system_agent.safe_executor.build_install_command",
            return_value=ExecutionResult(
                success=True,
                message="prepared",
                data={"command": ["missing-pkg-manager-xyz", "install", "demo"]},
            ),
        ):
            result = executor.execute("install_app", task)

        self.assertFalse(result.success)
        self.assertIn("Install executable not found", result.message)

    def test_install_action_retries_transient_failure_then_succeeds(self):
        executor = SafeExecutor()
        task = PlannedTask(action="install_app", target="vlc", raw_input="install vlc")

        with patch(
            "auto_system_agent.safe_executor.build_install_command",
            return_value=ExecutionResult(
                success=True,
                message="prepared",
                data={"command": ["apt", "install", "demo"]},
            ),
        ):
            with patch.dict(os.environ, {"AUTO_AGENT_INSTALL_RETRIES": "2"}, clear=False):
                with patch(
                    "auto_system_agent.safe_executor.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(args=["apt"], returncode=100, stdout="", stderr="Temporary failure resolving host"),
                        subprocess.CompletedProcess(args=["apt"], returncode=0, stdout="ok", stderr=""),
                    ],
                ):
                    result = executor.execute("install_app", task)

        self.assertTrue(result.success)
        self.assertIn("after retry", result.message)

    def test_install_action_fails_after_transient_retries_exhausted(self):
        executor = SafeExecutor()
        task = PlannedTask(action="install_app", target="vlc", raw_input="install vlc")

        with patch(
            "auto_system_agent.safe_executor.build_install_command",
            return_value=ExecutionResult(
                success=True,
                message="prepared",
                data={"command": ["apt", "install", "demo"]},
            ),
        ):
            with patch.dict(os.environ, {"AUTO_AGENT_INSTALL_RETRIES": "1"}, clear=False):
                with patch(
                    "auto_system_agent.safe_executor.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(args=["apt"], returncode=100, stdout="", stderr="Temporary failure resolving host"),
                        subprocess.CompletedProcess(args=["apt"], returncode=100, stdout="", stderr="Temporary failure resolving host"),
                    ],
                ):
                    result = executor.execute("install_app", task)

        self.assertFalse(result.success)
        self.assertIn("Installation failed with code", result.message)

    def test_compress_path_supports_single_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "note.txt"
            file_path.write_text("hello", encoding="utf-8")

            result = compress_path(str(file_path))

            self.assertTrue(result.success)
            archive_path = Path(result.message.split(": ", maxsplit=1)[1])
            self.assertTrue(archive_path.exists())

            with zipfile.ZipFile(archive_path, "r") as zip_obj:
                self.assertIn("note.txt", zip_obj.namelist())

    def test_create_folder_returns_error_when_mkdir_fails(self):
        with patch("auto_system_agent.tools.file_tool.Path.mkdir", side_effect=PermissionError("denied")):
            result = create_folder("demo")

        self.assertFalse(result.success)
        self.assertIn("Could not create folder", result.message)

    def test_delete_blocks_system_sensitive_paths(self):
        result = delete_path("/etc")

        self.assertFalse(result.success)
        self.assertIn("Deletion blocked", result.message)

    def test_delete_blocks_home_root(self):
        result = delete_path(str(Path.home()))

        self.assertFalse(result.success)
        self.assertIn("Deletion blocked", result.message)

    def test_delete_allows_non_protected_temp_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "delete-me.txt"
            target.write_text("demo", encoding="utf-8")

            result = delete_path(str(target))

            self.assertTrue(result.success)
            self.assertFalse(target.exists())

    def test_navigation_pwd_and_cd_commands(self):
        executor = SafeExecutor()
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir).resolve()
            child = parent / "child"
            child.mkdir()

            executor._working_directory = child
            result_pwd = executor.execute("run_command", PlannedTask(action="run_command", target="pwd"))
            self.assertTrue(result_pwd.success)
            self.assertEqual(result_pwd.message, str(child))

            result_cd_up = executor.execute("run_command", PlannedTask(action="run_command", target="cd .."))
            self.assertTrue(result_cd_up.success)
            self.assertIn(str(parent), result_cd_up.message)

            result_pwd_after = executor.execute("run_command", PlannedTask(action="run_command", target="pwd"))
            self.assertEqual(result_pwd_after.message, str(parent))

    def test_navigation_cd_home_and_ls(self):
        executor = SafeExecutor()
        result_cd_home = executor.execute("run_command", PlannedTask(action="run_command", target="cd ~"))
        self.assertTrue(result_cd_home.success)
        self.assertIn(str(Path.home().resolve()), result_cd_home.message)

        result_ls = executor.execute("run_command", PlannedTask(action="run_command", target="ls"))
        self.assertTrue(result_ls.success)
        self.assertIn("Contents of", result_ls.message)

    def test_file_management_mkdir_touch_cp_mv_rm_commands(self):
        executor = SafeExecutor()
        with tempfile.TemporaryDirectory() as temp_dir:
            executor._working_directory = Path(temp_dir).resolve()

            result_mkdir = executor.execute("run_command", PlannedTask(action="run_command", target="mkdir demo"))
            self.assertTrue(result_mkdir.success)

            result_touch = executor.execute("run_command", PlannedTask(action="run_command", target="touch demo/a.txt"))
            self.assertTrue(result_touch.success)

            result_cp = executor.execute("run_command", PlannedTask(action="run_command", target="cp demo/a.txt demo/b.txt"))
            self.assertTrue(result_cp.success)

            result_mv = executor.execute("run_command", PlannedTask(action="run_command", target="mv demo/b.txt demo/c.txt"))
            self.assertTrue(result_mv.success)

            result_rm_file = executor.execute("run_command", PlannedTask(action="run_command", target="rm demo/c.txt"))
            self.assertTrue(result_rm_file.success)

    def test_rm_requires_recursive_flag_for_folder(self):
        executor = SafeExecutor()
        with tempfile.TemporaryDirectory() as temp_dir:
            executor._working_directory = Path(temp_dir).resolve()
            folder = Path(temp_dir) / "demo"
            folder.mkdir()

            result_rm = executor.execute("run_command", PlannedTask(action="run_command", target="rm demo"))
            self.assertFalse(result_rm.success)
            self.assertIn("without -r", result_rm.message)

            result_rm_recursive = executor.execute("run_command", PlannedTask(action="run_command", target="rm -r demo"))
            self.assertTrue(result_rm_recursive.success)

    def test_file_viewing_commands_cat_less_head_tail(self):
        executor = SafeExecutor()
        with tempfile.TemporaryDirectory() as temp_dir:
            executor._working_directory = Path(temp_dir).resolve()
            target = Path(temp_dir) / "notes.txt"
            target.write_text("\n".join([f"line {i}" for i in range(1, 21)]), encoding="utf-8")

            result_cat = executor.execute("run_command", PlannedTask(action="run_command", target="cat notes.txt"))
            self.assertTrue(result_cat.success)
            self.assertIn("line 1", result_cat.message)
            self.assertIn("line 20", result_cat.message)

            result_head = executor.execute("run_command", PlannedTask(action="run_command", target="head notes.txt"))
            self.assertTrue(result_head.success)
            self.assertIn("line 1", result_head.message)
            self.assertNotIn("line 20", result_head.message)

            result_tail = executor.execute("run_command", PlannedTask(action="run_command", target="tail notes.txt"))
            self.assertTrue(result_tail.success)
            self.assertIn("line 20", result_tail.message)
            self.assertNotIn("line 1", result_tail.message)

            result_less = executor.execute("run_command", PlannedTask(action="run_command", target="less notes.txt"))
            self.assertTrue(result_less.success)
            self.assertIn("line 1", result_less.message)


if __name__ == "__main__":
    unittest.main()
