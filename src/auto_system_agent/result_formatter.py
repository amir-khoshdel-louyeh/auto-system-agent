from auto_system_agent.models import ExecutionResult


class ResultFormatter:
    """Formats executor results for chat/CLI output."""

    def format(self, result: ExecutionResult) -> str:
        status = "SUCCESS" if result.success else "ERROR"
        return f"[{status}] {result.message}"
