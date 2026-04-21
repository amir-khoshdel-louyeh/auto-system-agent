from auto_system_agent.models import PlannedTask
from auto_system_agent.llm_tool_mapper import LLMToolMapper


class ToolSelector:
    """Resolves task actions to tool keys via OLLAMA only.

    No hard-coded deterministic or guarded fallback remains. The model
    decides the tool; local mapping only validates against the whitelist.
    """

    SUPPORTED_ACTIONS = {
        "run_command",
        "help",
    }

    def __init__(self, llm_mapper: LLMToolMapper | None = None) -> None:
        self._llm_mapper = llm_mapper or LLMToolMapper()

    def select(self, task: PlannedTask) -> str:
        # If the planner already produced a whitelisted action, trust it
        # only if the action was produced by the LLM (planner is LLM-only).
        # We still validate against the whitelist so that non-whitelisted
        # actions become unknown.
        if task.action in self.SUPPORTED_ACTIONS:
            return task.action

        # Otherwise ask OLLAMA to map the raw intent to a tool.
        llm_selected = self._llm_mapper.map_intent(task.raw_input, self.SUPPORTED_ACTIONS)
        if llm_selected in self.SUPPORTED_ACTIONS:
            return llm_selected

        return "unknown"
