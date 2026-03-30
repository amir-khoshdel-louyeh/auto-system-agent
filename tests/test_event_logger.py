import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.event_logger import EventLogger
from auto_system_agent.models import PlannedTask


class InMemoryLogger(EventLogger):
    def __init__(self) -> None:
        self.events = []

    def log(self, event):
        self.events.append(event)


class FakePlanner:
    def plan_tasks(self, user_input: str):
        return [PlannedTask(action="create_folder", target="demo", raw_input=user_input)]


class FakeSelector:
    SUPPORTED_ACTIONS = {"create_folder"}

    def select(self, task):
        return task.action


class FakeExecutor:
    def execute(self, tool_key, task):
        from auto_system_agent.models import ExecutionResult

        return ExecutionResult(success=True, message="ok")


class FakeAssistant:
    def resolve(self, user_text, allowed_actions, history):
        return None


class UnknownPlanner:
    def plan_tasks(self, user_input: str):
        return [PlannedTask(action="unknown", target=user_input, raw_input=user_input)]


class UnknownSelector:
    SUPPORTED_ACTIONS = {"create_folder", "run_command", "install_app", "list_files"}

    def select(self, task):
        return "unknown"


class EventLoggerTests(unittest.TestCase):
    def test_agent_writes_structured_event(self):
        logger = InMemoryLogger()
        agent = AutoSystemAgent(
            planner=FakePlanner(),
            selector=FakeSelector(),
            executor=FakeExecutor(),
            assistant=FakeAssistant(),
            event_logger=logger,
        )

        reply = agent.process("create folder demo")
        self.assertIn("[SUCCESS]", reply)
        self.assertEqual(len(logger.events), 1)

        event = logger.events[0]
        self.assertEqual(event["mode"], "deterministic")
        self.assertEqual(event["user_input"], "create folder demo")
        self.assertEqual(event["planned_tasks"][0]["action"], "create_folder")
        self.assertEqual(event["steps"][0]["tool"], "create_folder")
        self.assertEqual(event["steps"][0]["audit"]["decision"], "approved")
        self.assertEqual(event["steps"][0]["audit"]["reason"], "allowed_action")

    def test_jsonl_logger_writes_line(self):
        tmp_path = PROJECT_ROOT / "tests" / "tmp_events.jsonl"
        if tmp_path.exists():
            tmp_path.unlink()

        logger = EventLogger(log_path=tmp_path)
        logger.log({"mode": "test", "user_input": "hello"})

        self.assertTrue(tmp_path.exists())
        content = tmp_path.read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        self.assertEqual(parsed["mode"], "test")
        self.assertEqual(parsed["user_input"], "hello")
        self.assertIn("timestamp", parsed)

        tmp_path.unlink()

    def test_agent_logs_unresolved_intent_telemetry(self):
        logger = InMemoryLogger()
        agent = AutoSystemAgent(
            planner=UnknownPlanner(),
            selector=UnknownSelector(),
            assistant=FakeAssistant(),
            event_logger=logger,
        )

        reply = agent.process("do something mysterious")
        self.assertIn("I can help with general questions", reply)

        unresolved = [event for event in logger.events if event.get("mode") == "unresolved_intent"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["reason"], "llm_unresolved_after_deterministic")
        self.assertIn("unknown", unresolved[0]["planned_actions"])


if __name__ == "__main__":
    unittest.main()
