"""Step evaluator for the ReAct loop.

Combines fast deterministic rules with an optional LLM second opinion.
"""

import json
import re

from auto_system_agent.models import Evaluation, ExecutionResult, PlannedTask, ReActStep

_VALID_VERDICTS = {"done", "retry", "replan", "abort"}


class Evaluator:
    """Judge for a single executed command."""

    def __init__(self, config: dict | None = None, max_same_command_retries: int = 2) -> None:
        self._config = config or {}
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

        # Policy blocks are final: retrying a DENY burns loop iterations.
        if "blocked by safety policy" in message:
            return Evaluation(
                verdict="abort",
                reason=f"Blocked by safety policy; will not retry: {result.message[:300]}",
            )

        # User-cancelled waits must stop the loop, not retry.
        if "cancelled by user" in message:
            return Evaluation(verdict="abort", reason="Cancelled by user.")

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

    def evaluate_with_llm(
        self,
        user_input: str,
        task: PlannedTask,
        tool_key: str,
        result: ExecutionResult,
        scratchpad: list[ReActStep] | None = None,
    ) -> Evaluation:
        """Deterministic verdict first, LLM second opinion on retry/replan."""
        base = self.evaluate(user_input, task, tool_key, result, scratchpad)
        if base.verdict in ("done", "abort"):
            return base
        llm_verdict = self._judge_with_llm(user_input, task, result, scratchpad or [])
        return llm_verdict or base

    def _judge_with_llm(
        self,
        user_input: str,
        task: PlannedTask,
        result: ExecutionResult,
        scratchpad: list[ReActStep],
    ) -> Evaluation | None:
        try:
            from auto_system_agent.services.llm_ollama_client import ollama_chat
        except ImportError:
            return None

        history = ""
        for step in scratchpad[-3:]:
            history += f"- ran {step.task.target} -> {step.result.message[:200]}\n"
        messages = [
            {
                "role": "system",
                "content": 'You verify one shell step. Return ONLY JSON: {"verdict": "done|retry|replan|abort", "reason": "...", "fixed_command": "..."}. '
                "done means the output proves the user goal is met (including already-exists). "
                "retry means rerun with fixed_command. replan means a new approach is needed. "
                "abort means unsafe or impossible.",
            },
            {
                "role": "user",
                "content": f"Goal: {user_input}\nCommand: {task.target}\nOutput: {(result.message or '')[:2000]}\n"
                f"Success flag: {result.success}\nRecent attempts:\n{history or '(none)'}",
            },
        ]
        try:
            content = ollama_chat(messages, config=self._config or None)
        except Exception:
            return None
        return self._parse_llm_verdict(content)

    @staticmethod
    def _parse_llm_verdict(content: str | None) -> Evaluation | None:
        if not content or not content.strip():
            return None
        text = content.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        verdict = str(parsed.get("verdict", "")).strip().lower()
        if verdict not in _VALID_VERDICTS:
            return None
        return Evaluation(
            verdict=verdict,
            reason=str(parsed.get("reason", "")).strip()[:500],
            fixed_command=str(parsed.get("fixed_command", "")).strip()[:500],
        )
