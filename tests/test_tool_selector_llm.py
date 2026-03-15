import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.models import PlannedTask
from auto_system_agent.tool_selector import ToolSelector


class FakeMapper:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def map_intent(self, user_text, allowed_actions):
        self.calls += 1
        return self.value


class ToolSelectorLLMTests(unittest.TestCase):
    def test_deterministic_path_wins_for_known_action(self):
        mapper = FakeMapper("delete_path")
        selector = ToolSelector(llm_mapper=mapper)

        task = PlannedTask(action="create_folder", target="demo", raw_input="create folder demo")
        self.assertEqual(selector.select(task), "create_folder")
        self.assertEqual(mapper.calls, 0)

    def test_llm_is_used_for_unknown_action(self):
        mapper = FakeMapper("list_files")
        selector = ToolSelector(llm_mapper=mapper)

        task = PlannedTask(action="unknown", raw_input="show me files")
        self.assertEqual(selector.select(task), "list_files")
        self.assertEqual(mapper.calls, 1)

    def test_falls_back_to_unknown_when_llm_unavailable(self):
        mapper = FakeMapper(None)
        selector = ToolSelector(llm_mapper=mapper)

        task = PlannedTask(action="unknown", raw_input="something random")
        self.assertEqual(selector.select(task), "unknown")

    def test_whitelist_blocks_untrusted_llm_action(self):
        mapper = FakeMapper("format_disk")
        selector = ToolSelector(llm_mapper=mapper)

        task = PlannedTask(action="unknown", raw_input="wipe this machine")
        self.assertEqual(selector.select(task), "unknown")


if __name__ == "__main__":
    unittest.main()
