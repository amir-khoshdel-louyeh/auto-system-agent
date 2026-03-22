import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.planner import Planner
from auto_system_agent.models import PlannedTask
from auto_system_agent.safe_executor import SafeExecutor


class FakePlanner:
    def __init__(self, action: str, target: str = "") -> None:
        self._action = action
        self._target = target

    def plan(self, user_input: str) -> PlannedTask:
        return PlannedTask(action=self._action, target=self._target, raw_input=user_input)

    def plan_tasks(self, user_input: str) -> list[PlannedTask]:
        return [self.plan(user_input)]


class FakeAssistant:
    def __init__(self, result=None):
        self.result = result

    def resolve(self, user_text, allowed_actions, history):
        return self.result


class FakeSelector:
    SUPPORTED_ACTIONS = {"create_folder", "list_files"}

    def select(self, task):
        return task.action


class FakeExecutor:
    def execute(self, tool_key, task):
        from auto_system_agent.models import ExecutionResult

        return ExecutionResult(success=True, message=f"{tool_key}:{task.target}")


class CapturingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, tool_key, task):
        from auto_system_agent.models import ExecutionResult

        self.calls.append((tool_key, task.target, dict(task.options)))
        return ExecutionResult(success=True, message=f"{tool_key}:{task.target}")


class PassThroughSelector:
    SUPPORTED_ACTIONS = {
        "install_app",
        "create_folder",
        "compress",
        "move_path",
        "delete_path",
        "list_files",
        "run_command",
        "help",
    }

    def select(self, task):
        return task.action if task.action in self.SUPPORTED_ACTIONS else "unknown"


class AgentConversationTests(unittest.TestCase):
    def test_handles_empty_planner_task_list(self):
        class EmptyPlanner:
            def plan_tasks(self, user_input):
                return []

        agent = AutoSystemAgent(
            planner=EmptyPlanner(),
            assistant=FakeAssistant(None),
        )

        response = agent.process("hello")
        self.assertIn("I can help with general questions", response)

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

    def test_uses_default_message_when_llm_unavailable(self):
        planner = FakePlanner("unknown")
        assistant = FakeAssistant(None)
        agent = AutoSystemAgent(planner=planner, assistant=assistant)

        response = agent.process("i want a video player. what is your suggestion?")
        self.assertIn("I can help with general questions", response)

    def test_runs_multi_step_tasks_sequentially(self):
        class MultiPlanner:
            def plan_tasks(self, user_input):
                return [
                    PlannedTask(action="create_folder", target="demo", raw_input=user_input),
                    PlannedTask(action="list_files", target=".", raw_input=user_input),
                ]

        agent = AutoSystemAgent(
            planner=MultiPlanner(),
            selector=FakeSelector(),
            executor=FakeExecutor(),
            assistant=FakeAssistant(None),
        )

        response = agent.process("create folder demo then list files in .")
        self.assertIn("Step 1: [SUCCESS] create_folder:demo", response)
        self.assertIn("Step 2: [SUCCESS] list_files:.", response)

    def test_reports_multi_step_progress_updates(self):
        class MultiPlanner:
            def plan_tasks(self, user_input):
                return [
                    PlannedTask(action="create_folder", target="demo", raw_input=user_input),
                    PlannedTask(action="list_files", target=".", raw_input=user_input),
                ]

        updates = []
        agent = AutoSystemAgent(
            planner=MultiPlanner(),
            selector=FakeSelector(),
            executor=FakeExecutor(),
            assistant=FakeAssistant(None),
        )

        agent.process("create folder demo then list files in .", progress_callback=updates.append)
        self.assertTrue(any("Step 1/2: running create_folder" in item for item in updates))
        self.assertTrue(any("Step 2/2: running list_files" in item for item in updates))

    def test_resolves_install_it_from_previous_chat_suggestion(self):
        class SequenceAssistant:
            def __init__(self):
                self.calls = 0

            def resolve(self, user_text, allowed_actions, history):
                self.calls += 1
                if self.calls == 1:
                    return {"type": "chat", "response": "VLC is a strong video player choice."}
                return None

        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=Planner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=SequenceAssistant(),
        )

        first_response = agent.process("suggest a video player")
        self.assertIn("VLC", first_response)

        second_response = agent.process("install it")
        self.assertIn("Confirmation required", second_response)

        third_response = agent.process("yes")
        self.assertIn("[SUCCESS] install_app:vlc", third_response)
        self.assertEqual(executor.calls[-1][1], "vlc")

    def test_confirmation_cancel_skips_execution(self):
        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=Planner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=FakeAssistant(None),
        )

        prompt = agent.process("install vlc")
        self.assertIn("Confirmation required", prompt)

        cancel_reply = agent.process("no")
        self.assertIn("Cancelled pending action", cancel_reply)
        self.assertEqual(len(executor.calls), 0)

    def test_confirmation_helper_methods(self):
        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=Planner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=FakeAssistant(None),
        )

        prompt = agent.process("install vlc")
        self.assertIn("Confirmation required", prompt)
        self.assertTrue(agent.has_pending_confirmation())

        reply = agent.confirm_pending()
        self.assertIsNotNone(reply)
        self.assertIn("[SUCCESS]", reply)
        self.assertFalse(agent.has_pending_confirmation())

    def test_pending_confirmation_summary_reflects_waiting_action(self):
        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=Planner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=FakeAssistant(None),
        )

        prompt = agent.process("install vlc")
        self.assertIn("Confirmation required", prompt)
        self.assertEqual(agent.get_pending_confirmation_summary(), "install_app vlc")

        agent.process("no")
        self.assertEqual(agent.get_pending_confirmation_summary(), "")

    def test_resolves_compress_it_in_multi_step_flow(self):
        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=Planner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=FakeAssistant(None),
        )

        response = agent.process("create folder demo then compress it")
        self.assertIn("Step 1: [SUCCESS] create_folder:demo", response)
        self.assertIn("Step 2: [SUCCESS] compress:demo", response)
        self.assertEqual(executor.calls[1][1], "demo")

    def test_multi_step_stops_when_blocked_command_fails(self):
        agent = AutoSystemAgent(
            planner=Planner(),
            selector=PassThroughSelector(),
            executor=SafeExecutor(),
            assistant=FakeAssistant(None),
        )

        prompt = agent.process("run echo hello then run python3 --version")
        self.assertIn("Confirmation required", prompt)

        response = agent.process("yes")
        self.assertIn("Step 1: [SUCCESS]", response)
        self.assertIn("Step 2: [ERROR]", response)
        self.assertIn("blocked by safety policy", response)


if __name__ == "__main__":
    unittest.main()
