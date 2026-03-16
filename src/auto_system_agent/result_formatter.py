from auto_system_agent.models import ExecutionResult


class ResultFormatter:
    """Formats executor results for chat/CLI output."""

    def format(self, result: ExecutionResult) -> str:
        status = "SUCCESS" if result.success else "ERROR"
        return f"[{status}] {result.message}"

    def format_many(self, results: list[ExecutionResult]) -> str:
        if len(results) == 1:
            return self.format(results[0])

        lines = []
        for index, result in enumerate(results, start=1):
            lines.append(f"Step {index}: {self.format(result)}")
        return "\n".join(lines)
