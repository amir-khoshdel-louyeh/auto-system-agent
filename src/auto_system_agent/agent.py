from auto_system_agent.planner import Planner
from auto_system_agent.result_formatter import ResultFormatter
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tool_selector import ToolSelector


class AutoSystemAgent:
    """End-to-end orchestration for one user request."""

    def __init__(self) -> None:
        self._planner = Planner()
        self._selector = ToolSelector()
        self._executor = SafeExecutor()
        self._formatter = ResultFormatter()

    def process(self, user_input: str) -> str:
        task = self._planner.plan(user_input)
        tool_key = self._selector.select(task)
        result = self._executor.execute(tool_key, task)
        return self._formatter.format(result)