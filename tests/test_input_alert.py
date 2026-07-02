import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.ui.real_terminal import RealTerminalFrame


def _frame_without_display():
    frame = RealTerminalFrame.__new__(RealTerminalFrame)
    frame._last_bell_at = 0.0
    calls: list[str] = []
    frame.bell = lambda: calls.append("bell")  # type: ignore[method-assign]
    return frame, calls


class InputAlertTests(unittest.TestCase):
    def test_sudo_prompt_bells(self):
        frame, calls = _frame_without_display()
        self.assertTrue(frame._maybe_alert("[sudo] password for amir: "))
        self.assertEqual(calls, ["bell"])

    def test_confirm_prompt_bells(self):
        frame, calls = _frame_without_display()
        self.assertTrue(frame._maybe_alert("Is this ok [y/N]: "))
        self.assertEqual(calls, ["bell"])

    def test_password_line_bells(self):
        frame, calls = _frame_without_display()
        self.assertTrue(frame._maybe_alert("Password:"))
        self.assertEqual(calls, ["bell"])

    def test_normal_output_silent(self):
        frame, calls = _frame_without_display()
        self.assertFalse(frame._maybe_alert("Last login: Tue Sep 16 10:00\n$ echo hi\nhi\n"))
        self.assertEqual(calls, [])

    def test_streaming_prompt_bells_once(self):
        frame, calls = _frame_without_display()
        self.assertTrue(frame._maybe_alert("[sudo] password"))
        # Same prompt arriving in pieces stays silent within the debounce window.
        self.assertFalse(frame._maybe_alert("[sudo] password for amir: "))
        self.assertEqual(calls, ["bell"])

    def test_bell_rings_again_after_window(self):
        import time

        frame, calls = _frame_without_display()
        from auto_system_agent.ui import real_terminal

        frame._maybe_alert("[sudo] password for amir: ")
        frame._last_bell_at = time.monotonic() - real_terminal._BELL_DEBOUNCE_SECONDS - 1
        self.assertTrue(frame._maybe_alert("[sudo] password for amir: "))
        self.assertEqual(calls, ["bell", "bell"])


if __name__ == "__main__":
    unittest.main()
