import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.models import ExecutionResult, PlannedTask
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tools.command_tool import run_command


class ExecutionSafetyTests(unittest.TestCase):
    def test_run_command_handles_invalid_shell_syntax(self):
        result = run_command('echo "unterminated')

        self.assertFalse(result.success)
        self.assertIn("Invalid command syntax", result.message)

    def test_run_command_handles_missing_executable(self):
        result = run_command("definitely-not-a-real-binary-xyz")

        self.assertFalse(result.success)
        self.assertIn("Command not found", result.message)

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


if __name__ == "__main__":
    unittest.main()
