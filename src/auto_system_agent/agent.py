from dataclasses import replace

from auto_system_agent.llm_conversation_assistant import LLMConversationAssistant
from auto_system_agent.llm_tool_mapper import LLMToolMapper
from auto_system_agent.models import ExecutionResult
from auto_system_agent.models import PlannedTask
from auto_system_agent.planner import Planner
from auto_system_agent.result_formatter import ResultFormatter
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tool_selector import ToolSelector
from auto_system_agent.tools.install_tool import extract_known_apps


APP_REFERENCE_WORDS = {"it", "that", "this", "one", "best one", "the best one", "one of them"}
PATH_REFERENCE_WORDS = {"it", "that", "this", "them", "there"}


class AutoSystemAgent:
    """End-to-end orchestration for one user request."""

    def __init__(
        self,
        planner: Planner | None = None,
        selector: ToolSelector | None = None,
        executor: SafeExecutor | None = None,
        formatter: ResultFormatter | None = None,
        assistant: LLMConversationAssistant | None = None,
        llm_config: dict | None = None,
    ) -> None:
        llm_mapper = LLMToolMapper(config=llm_config)
        self._planner = planner or Planner()
        self._selector = selector or ToolSelector(llm_mapper=llm_mapper)
        self._executor = executor or SafeExecutor()
        self._formatter = formatter or ResultFormatter()
        self._assistant = assistant or LLMConversationAssistant(config=llm_config)
        self._history: list[dict[str, str]] = []
        self._context: dict[str, str] = {"last_app": "", "last_path": ""}

    def process(self, user_input: str) -> str:
        tasks = self._planner.plan_tasks(user_input)
        if len(tasks) > 1:
            reply = self._process_multi_step(user_input, tasks)
            self._remember(user_input, reply)
            return reply

        task = tasks[0]
        task = self._resolve_task_context(task)
        tool_key = self._selector.select(task)

        if tool_key != "unknown":
            result = self._executor.execute(tool_key, task)
            self._update_context_from_task(task, result)
            reply = self._formatter.format(result)
            self._remember(user_input, reply)
            return reply

        allowed_actions = set(self._selector.SUPPORTED_ACTIONS)
        llm_result = self._assistant.resolve(user_input, allowed_actions, self._history)

        if llm_result and llm_result.get("type") == "chat":
            reply = llm_result["response"]
            self._update_context_from_chat(reply)
            self._remember(user_input, reply)
            return reply

        if llm_result and llm_result.get("type") == "tool":
            llm_task = PlannedTask(
                action=llm_result["action"],
                target=llm_result.get("target", "") or "",
                raw_input=user_input,
                options={"destination": llm_result.get("destination", "")},
            )
            llm_task = self._resolve_task_context(llm_task)
            llm_tool_key = self._selector.select(llm_task)
            result = self._executor.execute(llm_tool_key, llm_task)
            self._update_context_from_task(llm_task, result)
            reply = self._formatter.format(result)
            self._remember(user_input, reply)
            return reply

        reply = (
            "I can help with general questions and system tasks. "
            "Ask me about apps, or request actions like install, create folder, "
            "compress, move, delete, or run a command."
        )
        self._remember(user_input, reply)
        return reply

    def _process_multi_step(self, user_input: str, tasks: list[PlannedTask]) -> str:
        results: list[ExecutionResult] = []
        for task in tasks:
            task = self._resolve_task_context(task)
            tool_key = self._selector.select(task)
            if tool_key == "unknown":
                results.append(
                    ExecutionResult(
                        success=False,
                        message=f"Could not map step to a supported tool: {task.target or task.raw_input}",
                    )
                )
                break

            result = self._executor.execute(tool_key, task)
            results.append(result)
            self._update_context_from_task(task, result)
            if not result.success:
                break

        return self._formatter.format_many(results)

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