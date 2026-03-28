import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.llm_conversation_assistant import LLMConversationAssistant
from auto_system_agent.llm_tool_mapper import LLMToolMapper


class LLMClientBehaviorTests(unittest.TestCase):
    def test_tool_mapper_treats_none_as_no_tool(self):
        mapper = LLMToolMapper(config={"url": "http://example.local"})
        mapper._post_json = lambda payload: {"action": "none"}  # type: ignore[attr-defined]

        action = mapper.map_intent("how are you?", {"install_app", "run_command"})
        self.assertIsNone(action)

    def test_conversation_assistant_accepts_plain_text_reply(self):
        assistant = LLMConversationAssistant(config={"url": "http://example.local"})
        assistant._post_json = lambda payload: {
            "choices": [
                {"message": {"content": "VLC is a solid choice for most users."}}
            ]
        }  # type: ignore[attr-defined]

        result = assistant.resolve("suggest a video player", {"install_app"}, [])
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "chat")
        self.assertIn("VLC", result["response"])

    def test_tool_mapper_rejects_invalid_schema_payload(self):
        mapper = LLMToolMapper(config={"url": "http://example.local"})
        mapper._post_json = lambda payload: {"action": 123}  # type: ignore[attr-defined]

        action = mapper.map_intent("install vlc", {"install_app"})
        self.assertIsNone(action)

    def test_conversation_assistant_rejects_invalid_tool_schema(self):
        assistant = LLMConversationAssistant(config={"url": "http://example.local"})
        assistant._post_json = lambda payload: {"type": "tool", "action": "install_app", "target": 99}  # type: ignore[attr-defined]

        result = assistant.resolve("install vlc", {"install_app"}, [])
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
