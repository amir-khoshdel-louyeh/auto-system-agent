"""Deterministic step evaluator for the ReAct loop.

Decides done / retry / replan / abort from the raw ExecutionResult
without calling the LLM. The LLM verdict is layered on later.
"""

from auto_system_agent.models import Evaluation, ExecutionResult, PlannedTask, ReActStep


class Evaluator:
    """Rule-based judge for a single executed command."""

    def __init__(self, max_same_command_retries: int = 2) -> None:
        self._max_same_command_retries = max(1, max_same_command_retries)

    def evaluate(
        self,
        user_input: str,
        task: PlannedTask,
        tool_key: str,
        result: ExecutionResult,
        scratchpad: list[ReActStep] | None = None,
    ) -> Evaluation:
        _ = user_input
        scratchpad = scratchpad or []

        if tool_key == "unknown":
            return Evaluation(
                verdict="replan",
                reason=f"Could not map to a supported tool: {task.target or task.raw_input}",
            )

        message = (result.message or "").lower()

        if result.success:
            return Evaluation(verdict="done", reason="Command reported success.")

        # Idempotent goals: target state already holds.
        if "already exists" in message or "file exists" in message:
            return Evaluation(verdict="done", reason="Target state already exists.")

        # Safety stop: do not loop on permission problems.
        if "permission denied" in message:
            return Evaluation(
                verdict="abort",
                reason="Permission denied; retrying could escalate privileges.",
            )

        command = (task.target or "").strip()
        if self._repeated_failures(command, scratchpad):
            return Evaluation(
                verdict="replan",
                reason=f"Command failed repeatedly: {command}",
            )

        if "not found" in message or "no such file" in message or "no such directory" in message:
            return Evaluation(
                verdict="replan",
                reason=f"Command references something missing: {result.message[:300]}",
            )

        return Evaluation(
            verdict="retry",
            reason=f"Command failed: {result.message[:300]}",
            fixed_command=command,
        )

    def _repeated_failures(self, command: str, scratchpad: list[ReActStep]) -> bool:
        if not command:
            return False
        failures = sum(
            1
            for step in scratchpad
            if (step.task.target or "").strip() == command and not step.result.success
        )
        return failures >= self._max_same_command_retries
