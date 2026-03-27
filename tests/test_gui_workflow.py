import queue
import sys
import time
import unittest
from pathlib import Path

import tkinter as tk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.gui import AgentChatGUI
from auto_system_agent.models import ExecutionResult, PlannedTask, StepStatus


class InMemoryLogger:
    def __init__(self) -> None:
        self.events = []

    def log(self, event):
        self.events.append(event)


class FakeAssistant:
    def resolve(self, user_text, allowed_actions, history):
        return None


class DummyAgent:
    def has_pending_confirmation(self):
        return False

    def get_pending_confirmation_summary(self):
        return ""

    def cancel_pending(self):
        return ""


class FakeRoot:
    def after(self, _delay_ms, _callback):
        return None


class FakeButton:
    def __init__(self, state=tk.NORMAL):
        self._state = state

    def configure(self, **kwargs):
        if "state" in kwargs:
            self._state = kwargs["state"]

    def __getitem__(self, key):
        if key == "state":
            return self._state
        raise KeyError(key)


class FakeEntry:
    def __init__(self, text=""):
        self.text = text
        self.focus_calls = 0
        self.state = tk.NORMAL

    def get(self):
        return self.text

    def delete(self, _start, _end):
        self.text = ""

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def focus_set(self):
        self.focus_calls += 1


class FakeLabel:
    def __init__(self):
        self.text = ""
        self.fg = ""

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "fg" in kwargs:
            self.fg = kwargs["fg"]


class SingleStepPlanner:
    def __init__(self):
        self.calls = []

    def plan_tasks(self, user_input):
        self.calls.append(user_input)
        return [PlannedTask(action="create_folder", target="demo", raw_input=user_input)]


class MultiStepPlanner:
    def __init__(self):
        self.calls = []

    def plan_tasks(self, user_input):
        self.calls.append(user_input)
        return [
            PlannedTask(action="create_folder", target="demo", raw_input=user_input),
            PlannedTask(action="list_files", target="demo", raw_input=user_input),
        ]


class SpySelector:
    SUPPORTED_ACTIONS = {
        "create_folder",
        "list_files",
    }

    def __init__(self):
        self.calls = []

    def select(self, task):
        self.calls.append((task.action, task.target))
        return task.action


class SpyExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, tool_key, task):
        self.calls.append((tool_key, task.target))
        return ExecutionResult(success=True, message=f"{tool_key}:{task.target}")


class SpyFormatter:
    def __init__(self):
        self.format_calls = []
        self.format_many_calls = []

    def format(self, result):
        self.format_calls.append(result.message)
        return f"[SUCCESS] {result.message}"

    def format_many(self, results):
        self.format_many_calls.append([item.message for item in results])
        return "\n".join(f"Step {idx + 1}: [SUCCESS] {item.message}" for idx, item in enumerate(results))


def build_gui_harness(agent, user_text):
    gui = AgentChatGUI.__new__(AgentChatGUI)
    gui.agent = agent
    gui._is_busy = False
    gui._ui_queue = queue.Queue()
    gui.root = FakeRoot()
    gui.entry = FakeEntry(user_text)
    gui.send_button = FakeButton(state=tk.NORMAL)
    gui.confirm_button = FakeButton(state=tk.DISABLED)
    gui.cancel_button = FakeButton(state=tk.DISABLED)
    gui.confirmation_status_label = FakeLabel()
    gui.confirmation_details_label = FakeLabel()
    gui._step_progress_rows = {}
    gui._request_counter = 0
    gui._active_request_id = None
    gui._cancelled_request_ids = set()
    gui._request_started_at = None
    gui._task_timeout_seconds = 45.0

    messages = []
    progress_updates = []

    def append_message(speaker, message):
        messages.append((speaker, message))

    def update_progress(status):
        progress_updates.append(status)

    gui._append_message = append_message
    gui._update_progress_panel = update_progress
    gui._reset_progress_panel = lambda: progress_updates.clear()

    return gui, messages, progress_updates


def drain_until_idle(gui, timeout=1.0):
    deadline = time.time() + timeout
    while gui._is_busy and time.time() < deadline:
        gui._drain_ui_queue()
        time.sleep(0.01)

    gui._drain_ui_queue()
    return not gui._is_busy


class GUIWorkflowIntegrationTests(unittest.TestCase):
    def test_on_send_runs_single_step_pipeline_and_displays_response(self):
        planner = SingleStepPlanner()
        selector = SpySelector()
        executor = SpyExecutor()
        formatter = SpyFormatter()

        agent = AutoSystemAgent(
            planner=planner,
            selector=selector,
            executor=executor,
            formatter=formatter,
            assistant=FakeAssistant(),
            event_logger=InMemoryLogger(),
        )
        gui, messages, _progress = build_gui_harness(agent, "create folder demo")

        gui._on_send()
        self.assertTrue(drain_until_idle(gui), "GUI worker did not finish in time")

        self.assertEqual(planner.calls, ["create folder demo"])
        self.assertEqual(selector.calls, [("create_folder", "demo")])
        self.assertEqual(executor.calls, [("create_folder", "demo")])
        self.assertEqual(formatter.format_calls, ["create_folder:demo"])
        self.assertEqual(formatter.format_many_calls, [])

        self.assertIn(("You", "create folder demo"), messages)
        self.assertTrue(any(speaker == "System" and "running create_folder" in text.lower() for speaker, text in messages))
        self.assertIn(("Agent", "[SUCCESS] create_folder:demo"), messages)

    def test_on_send_runs_multi_step_pipeline_and_displays_final_summary(self):
        planner = MultiStepPlanner()
        selector = SpySelector()
        executor = SpyExecutor()
        formatter = SpyFormatter()

        agent = AutoSystemAgent(
            planner=planner,
            selector=selector,
            executor=executor,
            formatter=formatter,
            assistant=FakeAssistant(),
            event_logger=InMemoryLogger(),
        )
        gui, messages, progress_updates = build_gui_harness(agent, "create folder demo then list files in demo")

        gui._on_send()
        self.assertTrue(drain_until_idle(gui), "GUI worker did not finish in time")

        self.assertEqual(planner.calls, ["create folder demo then list files in demo"])
        self.assertEqual(
            selector.calls,
            [("create_folder", "demo"), ("list_files", "demo")],
        )
        self.assertEqual(
            executor.calls,
            [("create_folder", "demo"), ("list_files", "demo")],
        )
        self.assertEqual(formatter.format_calls, [])
        self.assertEqual(formatter.format_many_calls, [["create_folder:demo", "list_files:demo"]])

        self.assertTrue(any(isinstance(item, StepStatus) and item.step == 1 and item.state == "running" for item in progress_updates))
        self.assertTrue(any(isinstance(item, StepStatus) and item.step == 2 and item.tool == "list_files" for item in progress_updates))
        self.assertTrue(any(speaker == "Agent" and "Step 2: [SUCCESS] list_files:demo" in text for speaker, text in messages))

    def test_confirmation_state_is_visible_and_buttons_are_controllable(self):
        from auto_system_agent.planner import Planner

        agent = AutoSystemAgent(
            planner=Planner(),
            assistant=FakeAssistant(),
            event_logger=InMemoryLogger(),
        )
        gui, messages, _progress = build_gui_harness(agent, "install vlc")

        gui._on_send()
        self.assertTrue(drain_until_idle(gui), "GUI worker did not finish in time")

        self.assertEqual(gui.confirm_button["state"], tk.NORMAL)
        self.assertEqual(gui.cancel_button["state"], tk.NORMAL)
        self.assertIn("Pending confirmation", gui.confirmation_status_label.text)
        self.assertIn("install_app vlc", gui.confirmation_details_label.text)

        gui._on_cancel()
        self.assertEqual(gui.confirm_button["state"], tk.DISABLED)
        self.assertEqual(gui.cancel_button["state"], tk.DISABLED)
        self.assertIn("No pending confirmation", gui.confirmation_status_label.text)
        self.assertIn(("Agent", "Cancelled pending action."), messages)

    def test_cancel_stops_waiting_for_running_request(self):
        gui, messages, _progress = build_gui_harness(agent=DummyAgent(), user_text="")

        def long_task(_on_progress):
            time.sleep(0.2)
            return "should be ignored"

        gui._start_background_task(long_task)
        gui._on_cancel()
        self.assertTrue(drain_until_idle(gui), "GUI worker did not settle after cancel")
        self.assertTrue(any(speaker == "System" and "Cancelled running request" in text for speaker, text in messages))
        self.assertFalse(any(speaker == "Agent" and "should be ignored" in text for speaker, text in messages))

    def test_timeout_stops_waiting_for_running_request(self):
        gui, messages, _progress = build_gui_harness(agent=DummyAgent(), user_text="")
        gui._task_timeout_seconds = 0.05

        def long_task(_on_progress):
            time.sleep(0.2)
            return "late response"

        gui._start_background_task(long_task)
        self.assertTrue(drain_until_idle(gui), "GUI worker did not settle after timeout")
        self.assertTrue(any(speaker == "System" and "timed out" in text.lower() for speaker, text in messages))
        self.assertFalse(any(speaker == "Agent" and "late response" in text for speaker, text in messages))


if __name__ == "__main__":
    unittest.main()
