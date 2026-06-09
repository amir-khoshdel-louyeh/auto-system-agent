import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.gui import AgentChatGUI

HINT = "Ask the agent..."


class StubEntry:
    def __init__(self, text=""):
        self.text = text
        self.fg = ""

    def get(self):
        return self.text

    def delete(self, _start, _end):
        self.text = ""

    def insert(self, _index, value):
        self.text = value

    def configure(self, **kwargs):
        if "fg" in kwargs:
            self.fg = kwargs["fg"]


def _gui_with_entry(text, has_placeholder):
    gui = AgentChatGUI.__new__(AgentChatGUI)
    gui.entry = StubEntry(text)
    gui._entry_placeholder = HINT
    gui._entry_has_placeholder = has_placeholder
    return gui


class EntryPlaceholderTests(unittest.TestCase):
    def test_keypress_clears_stale_hint(self):
        gui = _gui_with_entry(HINT, True)
        AgentChatGUI._on_entry_key_press(gui)
        self.assertEqual(gui.entry.get(), "")
        self.assertFalse(gui._entry_has_placeholder)

    def test_paste_clears_stale_hint(self):
        gui = _gui_with_entry(HINT, True)
        AgentChatGUI._on_entry_paste(gui)
        self.assertEqual(gui.entry.get(), "")
        self.assertFalse(gui._entry_has_placeholder)

    def test_text_typed_after_hint_is_recovered(self):
        gui = _gui_with_entry(HINT + "hello", True)
        self.assertEqual(AgentChatGUI._read_entry_text(gui), "hello")

    def test_hint_only_reads_empty(self):
        gui = _gui_with_entry(HINT, True)
        self.assertEqual(AgentChatGUI._read_entry_text(gui), "")

    def test_normal_text_untouched(self):
        gui = _gui_with_entry("how much free space?", False)
        self.assertEqual(AgentChatGUI._read_entry_text(gui), "how much free space?")

    def test_hint_without_flag_reads_empty(self):
        gui = _gui_with_entry(HINT, False)
        self.assertEqual(AgentChatGUI._read_entry_text(gui), "")


if __name__ == "__main__":
    unittest.main()
