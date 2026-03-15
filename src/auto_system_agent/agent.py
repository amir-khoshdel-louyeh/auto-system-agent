from auto_system_agent.llm_conversation_assistant import LLMConversationAssistant
from auto_system_agent.llm_tool_mapper import LLMToolMapper
from auto_system_agent.models import PlannedTask
from auto_system_agent.planner import Planner
from auto_system_agent.result_formatter import ResultFormatter
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tool_selector import ToolSelector


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

    def process(self, user_input: str) -> str:
        task = self._planner.plan(user_input)
        tool_key = self._selector.select(task)

        if tool_key != "unknown":
            result = self._executor.execute(tool_key, task)
            reply = self._formatter.format(result)
            self._remember(user_input, reply)
            return reply

        allowed_actions = set(self._selector.SUPPORTED_ACTIONS)
        llm_result = self._assistant.resolve(user_input, allowed_actions, self._history)

        if llm_result and llm_result.get("type") == "chat":
            reply = llm_result["response"]
            self._remember(user_input, reply)
            return reply

        if llm_result and llm_result.get("type") == "tool":
            llm_task = PlannedTask(
                action=llm_result["action"],
                target=llm_result.get("target", "") or "",
                raw_input=user_input,
                options={"destination": llm_result.get("destination", "")},
            )
            llm_tool_key = self._selector.select(llm_task)
            result = self._executor.execute(llm_tool_key, llm_task)
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

    def _remember(self, user_text: str, assistant_text: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        if len(self._history) > 20:
            self._history = self._history[-20:]