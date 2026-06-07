import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.command_guard import check_command, find_desktop_matches
from auto_system_agent.models import PlannedTask
from auto_system_agent.safe_executor import SafeExecutor


def _run(cmd: str) -> str | None:
    return check_command(cmd)


class CommandGuardTests(unittest.TestCase):
    def test_existing_binary_passes(self):
        self.assertIsNone(_run("ls -la /tmp"))

    def test_missing_binary_fails_fast(self):
        msg = _run("definitely-not-a-real-binary-xyz --version")
        self.assertIsNotNone(msg)
        self.assertIn("not found", msg.lower())

    def test_open_on_linux_hint(self):
        msg = _run("open outlook")
        self.assertIsNotNone(msg)
        self.assertIn("xdg-open", msg)

    def test_shell_builtins_pass(self):
        for cmd in ("cd /tmp", "history", "clear", "exit", "pwd"):
            self.assertIsNone(_run(cmd), cmd)

    def test_compound_starting_with_builtin_passes(self):
        self.assertIsNone(_run("cd /tmp && ls"))

    def test_env_assignment_prefix_passes(self):
        self.assertIsNone(_run("FOO=1 ls /tmp"))

    def test_xdg_open_url_passes(self):
        self.assertIsNone(_run("xdg-open https://example.com"))

    def test_xdg_open_existing_file_passes(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            self.assertIsNone(_run(f"xdg-open {tmp.name}"))

    def test_xdg_open_bare_word_suggests_search(self):
        msg = _run("xdg-open outlook")
        self.assertIsNotNone(msg)
        self.assertIn("No such file", msg)
        # Either installed-app matches or a search hint, depending on the machine.
        self.assertTrue("gtk-launch" in msg or "flatpak list" in msg, msg)

    def test_find_desktop_matches(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "my-outlook-pwa.desktop"
            entry.write_text("[Desktop Entry]\nName=Outlook PWA\nExec=chrome --app=x\n", encoding="utf-8")
            matches = find_desktop_matches("outlook", dirs=[Path(tmp)])
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0][0], "Outlook PWA")

    def test_executor_blocks_missing_binary_without_terminal(self):
        from unittest.mock import patch

        from auto_system_agent.terminal import TerminalSession

        executor = SafeExecutor()
        with patch.object(TerminalSession, "run") as mocked:
            result = executor.execute(
                "run_command", PlannedTask(action="run_command", target="open outlook")
            )
            self.assertFalse(result.success)
            self.assertIn("xdg-open", result.message)
            mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
