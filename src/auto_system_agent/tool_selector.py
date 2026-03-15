from auto_system_agent.models import PlannedTask
from auto_system_agent.llm_tool_mapper import LLMToolMapper


class ToolSelector:
    """Resolves task actions to tool keys."""

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

    def __init__(self, llm_mapper: LLMToolMapper | None = None) -> None:
        self._llm_mapper = llm_mapper or LLMToolMapper()

    def select(self, task: PlannedTask) -> str:
        deterministic = self._select_deterministic(task)
        if deterministic != "unknown":
            return deterministic

        llm_selected = self._llm_mapper.map_intent(task.raw_input, self.SUPPORTED_ACTIONS)
        if llm_selected in self.SUPPORTED_ACTIONS:
            return llm_selected

        return "unknown"

    def _select_deterministic(self, task: PlannedTask) -> str:
        if task.action in self.SUPPORTED_ACTIONS:
            return task.action
        return "unknown"
