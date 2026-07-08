import shlex
import subprocess

from auto_system_agent.safety.command_guard import (
    ALLOW_MAX,
    CONFIRM_MAX,
    assess_command,
    check_command,
    score_command,
)
from auto_system_agent.models import ExecutionResult

# Kept for import compatibility; decisions now come from command_guard
# (assess_command), which is the single source of safety truth.
COMMAND_TOOL_POLICY = {
    "blocked_separators": {"&&", "||", ";", "|"},
    "blocked_interpreters": {
        "bash", "dash", "fish", "ksh", "node", "perl", "pwsh", "powershell", "python", "python3", "ruby", "sh", "zsh"
    },
    "blocked_commands": {"rm", "shutdown", "reboot", "mkfs", "dd", "poweroff"},
    "blocked_arguments": {"--no-preserve-root", "-rf", "-fr"},
}

_VERDICT_TO_LEVEL = {"ALLOW": "low", "CONFIRM": "medium", "DENY": "high"}


def _risk_score(parts: list[str]) -> int:
    """Single-source risk score via command_guard (0-100)."""
    try:
        return score_command(shlex.join(parts))[0]
    except Exception:
        return 0


def _risk_level(score: int) -> str:
    if score <= ALLOW_MAX:
        return "low"
    if score <= CONFIRM_MAX:
        return "medium"
    return "high"


def _check_command_policy(parts: list[str]) -> ExecutionResult | None:
    """Single-source policy gate: preflight first, then DENY verdict."""
    text = shlex.join(parts)
    guard_message = check_command(text)
    if guard_message:
        score = _risk_score(parts)
        return ExecutionResult(
            success=False,
            message=guard_message,
            data={"policy_decision": "blocked", "policy_reason": "preflight", "risk_score": score, "risk_level": _risk_level(score)},
        )
    assessment = assess_command(text)
    if assessment["verdict"] == "DENY":
        reasons = "; ".join(assessment["reasons"]) or "deny-list"
        return ExecutionResult(
            success=False,
            message=(
                f"Blocked by safety policy ({assessment['score']}/100): {reasons}. "
                f"Canonical: {assessment['canonical_form'] or text}"
            ),
            data={
                "policy_decision": "blocked",
                "policy_reason": assessment["reasons"][0] if assessment["reasons"] else "deny-list",
                "risk_score": assessment["score"],
                "risk_level": _VERDICT_TO_LEVEL["DENY"],
                "verdict": "DENY",
                "canonical_form": assessment["canonical_form"],
            },
        )
    return None


def run_command(command_text: str) -> ExecutionResult:
    if not command_text.strip():
        return ExecutionResult(success=False, message="No command provided.")

    try:
        parts = shlex.split(command_text)
    except ValueError as exc:
        return ExecutionResult(success=False, message=f"Invalid command syntax: {exc}")

    if not parts:
        return ExecutionResult(success=False, message="No command provided.")

    policy_result = _check_command_policy(parts)
    if policy_result is not None:
        return policy_result

    assessment = assess_command(shlex.join(parts))
    risk = assessment["score"]
    verdict = assessment["verdict"]
    reason = "; ".join(assessment["reasons"]) or "allowed_command"

    try:
        completed = subprocess.run(parts, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ExecutionResult(success=False, message=f"Command not found: {parts[0]}")
    except PermissionError:
        return ExecutionResult(success=False, message=f"Permission denied while executing command: {parts[0]}")
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not execute command: {exc}")

    output = (completed.stdout or "") + (completed.stderr or "")

    if completed.returncode != 0:
        return ExecutionResult(
            success=False,
            message=f"Command failed with code {completed.returncode}.\n{output.strip()}",
            data={"policy_decision": "approved", "policy_reason": reason, "risk_score": risk, "risk_level": _risk_level(risk), "verdict": verdict, "canonical_form": assessment["canonical_form"]},
        )

    return ExecutionResult(
        success=True,
        message=output.strip() or "Command executed successfully.",
        data={"policy_decision": "approved", "policy_reason": reason, "risk_score": risk, "risk_level": _risk_level(risk), "verdict": verdict, "canonical_form": assessment["canonical_form"]},
    )
