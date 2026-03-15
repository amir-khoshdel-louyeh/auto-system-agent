import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.models import PlannedTask


class FakePlanner:
    def __init__(self, action: str, target: str = "") -> None:
        self._action = action
        self._target = target

    def plan(self, user_input: str) -> PlannedTask:
        return PlannedTask(action=self._action, target=self._target, raw_input=user_input)


class FakeAssistant:
    def __init__(self, result=None):
        self.result = result

    def resolve(self, user_text, allowed_actions, history):
        return self.result


class AgentConversationTests(unittest.TestCase):
    def test_returns_chat_response_for_unknown_intent(self):
        planner = FakePlanner("unknown")
        assistant = FakeAssistant({"type": "chat", "response": "Firefox is a privacy-focused browser."})
        agent = AutoSystemAgent(planner=planner, assistant=assistant)

        response = agent.process("which browser is privacy friendly?")
        self.assertEqual(response, "Firefox is a privacy-focused browser.")

    def test_executes_tool_when_llm_returns_whitelisted_action(self):
        planner = FakePlanner("unknown")
        assistant = FakeAssistant({"type": "tool", "action": "help", "target": ""})
        agent = AutoSystemAgent(planner=planner, assistant=assistant)

        response = agent.process("show me examples")
        self.assertIn("[SUCCESS]", response)

    def test_uses_friendly_message_when_nothing_matches(self):
        planner = FakePlanner("unknown")
        assistant = FakeAssistant(None)
        agent = AutoSystemAgent(planner=planner, assistant=assistant)

        response = agent.process("random text")
        self.assertIn("I can help with general questions", response)

    def test_local_fallback_handles_greeting(self):
        planner = FakePlanner("unknown")
        assistant = FakeAssistant(None)
        agent = AutoSystemAgent(planner=planner, assistant=assistant)

        response = agent.process("hi")
        self.assertIn("I can chat and help you", response)

    def test_local_fallback_handles_video_player_question(self):
        planner = FakePlanner("unknown")
        assistant = FakeAssistant(None)
        agent = AutoSystemAgent(planner=planner, assistant=assistant)

        response = agent.process("i want a video player. what is your suggestion?")
        self.assertIn("VLC", response)


if __name__ == "__main__":
    unittest.main()
