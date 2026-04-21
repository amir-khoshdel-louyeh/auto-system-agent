import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.models import StepStatus
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
    SUPPORTED_ACTIONS = {"run_command", "help"}

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
        self.assertIn("OLLAMA is not reachable", response)

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
        self.assertIn("OLLAMA is not reachable", response)

    def test_uses_default_message_when_llm_unavailable(self):
        planner = FakePlanner("unknown")
        assistant = FakeAssistant(None)
        agent = AutoSystemAgent(planner=planner, assistant=assistant)

        response = agent.process("i want a video player. what is your suggestion?")
        self.assertIn("OLLAMA is not reachable", response)

    def test_runs_multi_step_tasks_sequentially(self):
        class MultiPlanner:
            def plan_tasks(self, user_input):
                return [
                    PlannedTask(action="run_command", target="mkdir -p demo", raw_input=user_input),
                    PlannedTask(action="run_command", target="ls -la .", raw_input=user_input),
                ]

        agent = AutoSystemAgent(
            planner=MultiPlanner(),
            selector=FakeSelector(),
            executor=FakeExecutor(),
            assistant=FakeAssistant(None),
        )

        response = agent.process("create folder demo then list files in .")
        self.assertIn("Step 1: [SUCCESS] run_command:mkdir -p demo", response)
        self.assertIn("Step 2: [SUCCESS] run_command:ls -la .", response)

    def test_reports_multi_step_progress_updates(self):
        class MultiPlanner:
            def plan_tasks(self, user_input):
                return [
                    PlannedTask(action="run_command", target="mkdir -p demo", raw_input=user_input),
                    PlannedTask(action="run_command", target="ls -la .", raw_input=user_input),
                ]

        updates = []
        agent = AutoSystemAgent(
            planner=MultiPlanner(),
            selector=FakeSelector(),
            executor=FakeExecutor(),
            assistant=FakeAssistant(None),
        )

        agent.process("create folder demo then list files in .", progress_callback=updates.append)
        self.assertTrue(any(isinstance(item, StepStatus) and item.step == 1 and item.state == "running" for item in updates))
        self.assertTrue(any(isinstance(item, StepStatus) and item.step == 2 and item.tool == "run_command" for item in updates))

    def test_resolves_install_it_from_previous_chat_suggestion(self):
        class SequenceAssistant:
            def __init__(self):
                self.calls = 0

            def resolve(self, user_text, allowed_actions, history):
                self.calls += 1
                if self.calls == 1:
                    return {"type": "chat", "response": "VLC is a strong video player choice."}
                # Second call: OLLAMA resolves "install it" to run_command via history
                if "install it" in user_text.lower():
                    return {"type": "tool", "action": "run_command", "target": "sudo apt install -y vlc", "destination": ""}
                return None

        class SuggestThenInstallPlanner:
            def plan_tasks(self, user_input):
                if "install it" in user_input.lower():
                    return [PlannedTask(action="unknown", target="", raw_input=user_input)]
                return [PlannedTask(action="unknown", target="", raw_input=user_input)]

        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=SuggestThenInstallPlanner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=SequenceAssistant(),
        )

        first_response = agent.process("suggest a video player")
        self.assertIn("VLC", first_response)

        second_response = agent.process("install it")
        self.assertIn("Confirmation required", second_response)

        third_response = agent.process("yes")
        self.assertIn("[SUCCESS] run_command:sudo apt install -y vlc", third_response)
        self.assertEqual(executor.calls[-1][1], "sudo apt install -y vlc")

    def test_confirmation_cancel_skips_execution(self):
        class InstallPlanner:
            def plan_tasks(self, user_input):
                return [PlannedTask(action="run_command", target="sudo apt install -y vlc", raw_input=user_input)]

        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=InstallPlanner(),
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
        class InstallPlanner:
            def plan_tasks(self, user_input):
                return [PlannedTask(action="run_command", target="sudo apt install -y vlc", raw_input=user_input)]

        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=InstallPlanner(),
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

    def test_can_disable_high_risk_confirmation(self):
        class InstallPlanner:
            def plan_tasks(self, user_input):
                return [PlannedTask(action="run_command", target="sudo apt install -y vlc", raw_input=user_input)]

        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=InstallPlanner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=FakeAssistant(None),
            confirm_high_risk=False,
        )

        response = agent.process("install vlc")
        self.assertIn("[SUCCESS]", response)
        self.assertFalse(agent.has_pending_confirmation())
        self.assertEqual(executor.calls[-1][0], "run_command")

    def test_pending_confirmation_summary_reflects_waiting_action(self):
        class InstallPlanner:
            def plan_tasks(self, user_input):
                return [PlannedTask(action="run_command", target="sudo apt install -y vlc", raw_input=user_input)]

        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=InstallPlanner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=FakeAssistant(None),
        )

        prompt = agent.process("install vlc")
        self.assertIn("Confirmation required", prompt)
        self.assertEqual(agent.get_pending_confirmation_summary(), "run_command sudo apt install -y vlc")

        agent.process("no")
        self.assertEqual(agent.get_pending_confirmation_summary(), "")

    def test_resolves_compress_it_in_multi_step_flow(self):
        class CompressItPlanner:
            def plan_tasks(self, user_input):
                return [
                    PlannedTask(action="run_command", target="mkdir -p demo", raw_input=user_input),
                    PlannedTask(action="run_command", target="zip -r demo.zip demo", raw_input=user_input),
                ]

        executor = CapturingExecutor()
        agent = AutoSystemAgent(
            planner=CompressItPlanner(),
            selector=PassThroughSelector(),
            executor=executor,
            assistant=FakeAssistant(None),
        )

        response = agent.process("create folder demo then compress it")
        self.assertIn("Step 1: [SUCCESS] run_command:mkdir -p demo", response)
        self.assertIn("Step 2: [SUCCESS] run_command:zip -r demo.zip demo", response)
        self.assertEqual(executor.calls[1][1], "zip -r demo.zip demo")

    def test_multi_step_stops_when_blocked_command_fails(self):
        class MultiRunPlanner:
            def plan_tasks(self, user_input):
                return [
                    PlannedTask(action="run_command", target="echo hello", raw_input=user_input),
                    PlannedTask(action="run_command", target="ls /nonexistent_path_12345", raw_input=user_input),
                ]

        agent = AutoSystemAgent(
            planner=MultiRunPlanner(),
            selector=PassThroughSelector(),
            executor=SafeExecutor(),
            assistant=FakeAssistant(None),
        )

        # echo hello is safe (no confirmation), ls /nonexistent will be executed and fail
        response = agent.process("run echo hello then run ls nonexistent")
        # No confirmation needed for safe commands, goes directly to execution
        self.assertIn("Step 1: [SUCCESS]", response)
        self.assertIn("Step 2: [ERROR]", response)
        self.assertIn("No such file", response)


if __name__ == "__main__":
    unittest.main()
