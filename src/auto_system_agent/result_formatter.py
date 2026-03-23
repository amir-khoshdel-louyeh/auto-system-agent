from auto_system_agent.models import ExecutionResult


class ResultFormatter:
    """Formats executor results for chat/CLI output."""

    def format(self, result: ExecutionResult) -> str:
        status = "SUCCESS" if result.success else "ERROR"
        detail = ""
        if result.data.get("policy_decision") == "blocked":
            reason = str(result.data.get("policy_reason", "")).replace("_", " ").strip()
            risk_score = result.data.get("risk_score")
            risk_level = str(result.data.get("risk_level", "")).strip()
            if reason:
                detail = f" Reason: {reason}."
            if risk_score is not None or risk_level:
                detail += f" Risk: {risk_level or 'unknown'} ({risk_score if risk_score is not None else '?'}/100)."
        return f"[{status}] {result.message}{detail}"

    def format_many(self, results: list[ExecutionResult]) -> str:
        if len(results) == 1:
            return self.format(results[0])

        lines = []
        for index, result in enumerate(results, start=1):
            lines.append(f"Step {index}: {self.format(result)}")
        return "\n".join(lines)
