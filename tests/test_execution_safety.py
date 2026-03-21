import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

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

    def test_run_command_blocks_risky_flags(self):
        result = run_command("echo -rf")

        self.assertFalse(result.success)
        self.assertIn("blocked by safety policy", result.message)

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
        self.assertIn("Install command not found", result.message)

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


if __name__ == "__main__":
    unittest.main()
