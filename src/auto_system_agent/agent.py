from dataclasses import replace
from typing import Callable

from auto_system_agent.event_logger import EventLogger
from auto_system_agent.llm_conversation_assistant import LLMConversationAssistant
from auto_system_agent.llm_tool_mapper import LLMToolMapper
from auto_system_agent.models import ExecutionResult
from auto_system_agent.models import PlannedTask
from auto_system_agent.models import StepStatus
from auto_system_agent.planner import Planner
from auto_system_agent.result_formatter import ResultFormatter
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tool_selector import ToolSelector
from auto_system_agent.tools.install_tool import extract_known_apps


APP_REFERENCE_WORDS = {"it", "that", "this", "one", "best one", "the best one", "one of them"}
PATH_REFERENCE_WORDS = {"it", "that", "this", "them", "there"}
CONFIRMATION_YES_WORDS = {"yes", "y", "confirm", "ok", "proceed"}
CONFIRMATION_NO_WORDS = {"no", "n", "cancel", "stop"}
HIGH_RISK_ACTIONS = {"install_app", "delete_path", "run_command"}


class AutoSystemAgent:
    """End-to-end orchestration for one user request."""

    def __init__(
        self,
        planner: Planner | None = None,
        selector: ToolSelector | None = None,
        executor: SafeExecutor | None = None,
        formatter: ResultFormatter | None = None,
        assistant: LLMConversationAssistant | None = None,
        event_logger: EventLogger | None = None,
        llm_config: dict | None = None,
    ) -> None:
        llm_mapper = LLMToolMapper(config=llm_config)
        self._planner = planner or Planner()
        self._selector = selector or ToolSelector(llm_mapper=llm_mapper)
        self._executor = executor or SafeExecutor()
        self._formatter = formatter or ResultFormatter()
        self._assistant = assistant or LLMConversationAssistant(config=llm_config)
        self._event_logger = event_logger or EventLogger()
        self._history: list[dict[str, str]] = []
        self._context: dict[str, str] = {"last_app": "", "last_path": ""}
        self._pending_confirmation: dict | None = None

    def process(
        self,
        user_input: str,
        progress_callback: Callable[[StepStatus], None] | None = None,
    ) -> str:
        pending_reply = self._handle_pending_confirmation(user_input, progress_callback)
        if pending_reply is not None:
            return pending_reply

        tasks = self._planner.plan_tasks(user_input)
        if not tasks:
            reply = (
                "I can help with general questions and system tasks. "
                "Ask me about apps, or request actions like install, create folder, "
                "compress, move, delete, or run a command."
            )
            self._remember(user_input, reply)
            self._log_event(user_input=user_input, mode="fallback", planned_tasks=[], steps=[], reply=reply)
            return reply

        if len(tasks) > 1:
            tasks = [self._resolve_task_context(task) for task in tasks]
            if self._requires_confirmation_for_tasks(tasks):
                reply = self._queue_confirmation(user_input, tasks, source_mode="multi_step")
                self._remember(user_input, reply)
                self._log_event(user_input=user_input, mode="confirmation_requested", planned_tasks=tasks, steps=[], reply=reply)
                return reply

            reply, steps = self._process_multi_step(user_input, tasks, progress_callback)
            self._remember(user_input, reply)
            self._log_event(user_input=user_input, mode="multi_step", planned_tasks=tasks, steps=steps, reply=reply)
            return reply

        task = tasks[0]
        task = self._resolve_task_context(task)
        tool_key = self._selector.select(task)

        if tool_key != "unknown":
            if self._requires_confirmation_for_tasks([task]):
                reply = self._queue_confirmation(user_input, [task], source_mode="deterministic")
                self._remember(user_input, reply)
                self._log_event(user_input=user_input, mode="confirmation_requested", planned_tasks=[task], steps=[], reply=reply)
                return reply

            self._notify(progress_callback, StepStatus(step=1, total=1, tool=tool_key, state="running"))
            result = self._executor.execute(tool_key, task)
            self._notify(
                progress_callback,
                StepStatus(step=1, total=1, tool=tool_key, state="done" if result.success else "failed", message=result.message),
            )
            self._update_context_from_task(task, result)
            reply = self._formatter.format(result)
            self._remember(user_input, reply)
            self._log_event(
                user_input=user_input,
                mode="deterministic",
                planned_tasks=tasks,
                steps=[self._step_payload(tool_key, task, result)],
                reply=reply,
            )
            return reply

        allowed_actions = set(self._selector.SUPPORTED_ACTIONS)
        llm_result = self._assistant.resolve(user_input, allowed_actions, self._history)

        if llm_result and llm_result.get("type") == "chat":
            reply = llm_result["response"]
            self._update_context_from_chat(reply)
            self._remember(user_input, reply)
            self._log_event(user_input=user_input, mode="llm_chat", planned_tasks=tasks, steps=[], reply=reply)
            return reply

        if llm_result and llm_result.get("type") == "tool":
            llm_task = PlannedTask(
                action=llm_result["action"],
                target=llm_result.get("target", "") or "",
                raw_input=user_input,
                options={"destination": llm_result.get("destination", "")},
            )
            llm_task = self._resolve_task_context(llm_task)
            if self._requires_confirmation_for_tasks([llm_task]):
                reply = self._queue_confirmation(user_input, [llm_task], source_mode="llm_tool")
                self._remember(user_input, reply)
                self._log_event(user_input=user_input, mode="confirmation_requested", planned_tasks=[llm_task], steps=[], reply=reply)
                return reply

            llm_tool_key = self._selector.select(llm_task)
            self._notify(progress_callback, StepStatus(step=1, total=1, tool=llm_tool_key, state="running"))
            result = self._executor.execute(llm_tool_key, llm_task)
            self._notify(
                progress_callback,
                StepStatus(step=1, total=1, tool=llm_tool_key, state="done" if result.success else "failed", message=result.message),
            )
            self._update_context_from_task(llm_task, result)
            reply = self._formatter.format(result)
            self._remember(user_input, reply)
            self._log_event(
                user_input=user_input,
                mode="llm_tool",
                planned_tasks=tasks,
                steps=[self._step_payload(llm_tool_key, llm_task, result)],
                reply=reply,
            )
            return reply

        reply = (
            "I can help with general questions and system tasks. "
            "Ask me about apps, or request actions like install, create folder, "
            "compress, move, delete, or run a command."
        )
        self._remember(user_input, reply)
        self._log_event(user_input=user_input, mode="fallback", planned_tasks=tasks, steps=[], reply=reply)
        return reply

    def has_pending_confirmation(self) -> bool:
        return self._pending_confirmation is not None

    def get_pending_confirmation_summary(self) -> str:
        if not self._pending_confirmation:
            return ""

        tasks: list[PlannedTask] = self._pending_confirmation.get("tasks", [])
        if not tasks:
            return ""

        parts = []
        for task in tasks:
            parts.append(f"{task.action} {task.target or ''}".strip())
        return "; ".join(parts)

    def confirm_pending(self, progress_callback: Callable[[str], None] | None = None) -> str | None:
        return self._handle_pending_confirmation("yes", progress_callback)

    def cancel_pending(self) -> str | None:
        return self._handle_pending_confirmation("no", None)

    def _process_multi_step(
        self,
        user_input: str,
        tasks: list[PlannedTask],
        progress_callback: Callable[[StepStatus], None] | None = None,
    ) -> tuple[str, list[dict]]:
        results: list[ExecutionResult] = []
        step_payloads: list[dict] = []
        for index, task in enumerate(tasks, start=1):
            task = self._resolve_task_context(task)
            tool_key = self._selector.select(task)
            self._notify(progress_callback, StepStatus(step=index, total=len(tasks), tool=tool_key, state="running"))
            if tool_key == "unknown":
                unknown_result = ExecutionResult(
                    success=False,
                    message=f"Could not map step to a supported tool: {task.target or task.raw_input}",
                )
                results.append(unknown_result)
                step_payloads.append(self._step_payload(tool_key, task, unknown_result))
                self._notify(
                    progress_callback,
                    StepStatus(step=index, total=len(tasks), tool=tool_key, state="failed", message=unknown_result.message),
                )
                break

            result = self._executor.execute(tool_key, task)
            results.append(result)
            step_payloads.append(self._step_payload(tool_key, task, result))
            self._update_context_from_task(task, result)
            self._notify(
                progress_callback,
                StepStatus(
                    step=index,
                    total=len(tasks),
                    tool=tool_key,
                    state="done" if result.success else "failed",
                    message=result.message,
                ),
            )
            if not result.success:
                break

        return self._formatter.format_many(results), step_payloads

    def _resolve_task_context(self, task: PlannedTask) -> PlannedTask:
        target = (task.target or "").strip()

        if task.action == "install_app" and self._is_app_reference(target):
            last_app = self._context.get("last_app", "")
            if last_app:
                return replace(task, target=last_app)

        if task.action in {"compress", "delete_path", "list_files"} and self._is_path_reference(target):
            last_path = self._context.get("last_path", "")
            if last_path:
                return replace(task, target=last_path)

        if task.action == "move_path" and self._is_path_reference(target):
            last_path = self._context.get("last_path", "")
            if last_path:
                return replace(task, target=last_path)

        return task

    def _update_context_from_chat(self, reply: str) -> None:
        apps = extract_known_apps(reply)
        if apps:
            self._context["last_app"] = apps[0]

    def _update_context_from_task(self, task: PlannedTask, result: ExecutionResult) -> None:
        if not result.success:
            return

        if task.action == "install_app" and task.target:
            self._context["last_app"] = task.target
            return

        if task.action in {"create_folder", "compress", "list_files", "delete_path"} and task.target:
            self._context["last_path"] = task.target
            return

        if task.action == "move_path":
            destination = str(task.options.get("destination", "")).strip()
            if destination:
                self._context["last_path"] = destination

    def _is_app_reference(self, text: str) -> bool:
        normalized = text.lower().strip()
        return normalized in APP_REFERENCE_WORDS

    def _is_path_reference(self, text: str) -> bool:
        normalized = text.lower().strip()
        return normalized in PATH_REFERENCE_WORDS

    def _remember(self, user_text: str, assistant_text: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        if len(self._history) > 20:
            self._history = self._history[-20:]

    def _notify(self, callback: Callable[[StepStatus], None] | None, status: StepStatus) -> None:
        if callback:
            callback(status)

    def _handle_pending_confirmation(
        self,
        user_input: str,
        progress_callback: Callable[[StepStatus], None] | None,
    ) -> str | None:
        if not self._pending_confirmation:
            return None

        decision = user_input.strip().lower()
        if decision in CONFIRMATION_NO_WORDS:
            pending = self._pending_confirmation
            self._pending_confirmation = None
            reply = "Cancelled pending action."
            self._remember(user_input, reply)
            self._log_event(
                user_input=user_input,
                mode="confirmation_cancelled",
                planned_tasks=pending["tasks"],
                steps=[],
                reply=reply,
            )
            return reply

        if decision not in CONFIRMATION_YES_WORDS:
            reply = "Confirmation required. Reply 'yes' to continue or 'no' to cancel."
            self._remember(user_input, reply)
            return reply

        pending = self._pending_confirmation
        self._pending_confirmation = None
        tasks: list[PlannedTask] = pending["tasks"]
        source_input: str = pending["source_input"]

        if len(tasks) > 1:
            reply, steps = self._process_multi_step(source_input, tasks, progress_callback)
            self._remember(user_input, reply)
            self._log_event(
                user_input=source_input,
                mode="confirmed_multi_step",
                planned_tasks=tasks,
                steps=steps,
                reply=reply,
            )
            return reply

        task = tasks[0]
        tool_key = self._selector.select(task)
        if tool_key == "unknown":
            reply = self._formatter.format(
                ExecutionResult(success=False, message="Could not map request to a supported tool.")
            )
            self._remember(user_input, reply)
            return reply

        self._notify(progress_callback, StepStatus(step=1, total=1, tool=tool_key, state="running"))
        result = self._executor.execute(tool_key, task)
        self._notify(
            progress_callback,
            StepStatus(step=1, total=1, tool=tool_key, state="done" if result.success else "failed", message=result.message),
        )
        self._update_context_from_task(task, result)
        reply = self._formatter.format(result)
        self._remember(user_input, reply)
        self._log_event(
            user_input=source_input,
            mode="confirmed_single_step",
            planned_tasks=tasks,
            steps=[self._step_payload(tool_key, task, result)],
            reply=reply,
        )
        return reply

    def _queue_confirmation(self, user_input: str, tasks: list[PlannedTask], source_mode: str) -> str:
        self._pending_confirmation = {
            "source_input": user_input,
            "source_mode": source_mode,
            "tasks": tasks,
        }

        summary = "; ".join(
            f"{task.action} {task.target or ''}".strip() for task in tasks
        )
        return (
            "Confirmation required for high-risk action(s): "
            f"{summary}. Reply 'yes' to continue or 'no' to cancel."
        )

    def _requires_confirmation_for_tasks(self, tasks: list[PlannedTask]) -> bool:
        return any(task.action in HIGH_RISK_ACTIONS for task in tasks)

    def _step_payload(self, tool_key: str, task: PlannedTask, result: ExecutionResult) -> dict:
        decision = str(result.data.get("policy_decision", "")).strip() or "approved"
        if "blocked" in result.message.lower():
            decision = "blocked"
        reason = str(result.data.get("policy_reason", "")).strip() or ("safety_policy" if decision == "blocked" else "allowed_action")
        return {
            "tool": tool_key,
            "task": {
                "action": task.action,
                "target": task.target or "",
                "options": dict(task.options),
            },
            "audit": {
                "decision": decision,
                "reason": reason,
                "risk_score": result.data.get("risk_score"),
                "risk_level": result.data.get("risk_level", ""),
            },
            "result": {
                "success": result.success,
                "message": result.message,
            },
        }

    def _log_event(
        self,
        *,
        user_input: str,
        mode: str,
        planned_tasks: list[PlannedTask],
        steps: list[dict],
        reply: str,
    ) -> None:
        self._event_logger.log(
            {
                "mode": mode,
                "user_input": user_input,
                "planned_tasks": [
                    {
                        "action": task.action,
                        "target": task.target or "",
                        "options": dict(task.options),
                    }
                    for task in planned_tasks
                ],
                "steps": steps,
                "reply": reply,
            }
        )