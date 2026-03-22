import shlex
import subprocess
from pathlib import Path

from auto_system_agent.models import ExecutionResult

COMMAND_TOOL_POLICY = {
    "blocked_separators": {"&&", "||", ";", "|"},
    "blocked_interpreters": {
        "bash", "dash", "fish", "ksh", "node", "perl", "pwsh", "powershell", "python", "python3", "ruby", "sh", "zsh"
    },
    "blocked_commands": {"rm", "shutdown", "reboot", "mkfs", "dd", "poweroff"},
    "blocked_arguments": {"--no-preserve-root", "-rf", "-fr"},
}


def _risk_score(parts: list[str]) -> int:
    executable_name = Path(parts[0]).name.lower()
    read_only = {"ls", "pwd", "whoami", "date", "uname", "df", "du", "ps", "cat", "echo"}
    if executable_name in read_only:
        return 15
    return 45


def _risk_level(score: int) -> str:
    if score <= 25:
        return "low"
    if score <= 60:
        return "medium"
    return "high"


def _check_command_policy(parts: list[str]) -> ExecutionResult | None:
    risk = _risk_score(parts)

    if any(token in COMMAND_TOOL_POLICY["blocked_separators"] for token in parts):
        return ExecutionResult(
            success=False,
            message="Command chaining is blocked by safety policy.",
            data={"policy_decision": "blocked", "policy_reason": "command_chaining", "risk_score": risk, "risk_level": _risk_level(risk)},
        )

    executable_name = Path(parts[0]).name.lower()
    if executable_name in COMMAND_TOOL_POLICY["blocked_interpreters"]:
        return ExecutionResult(
            success=False,
            message="Interpreter and shell execution is blocked by safety policy.",
            data={"policy_decision": "blocked", "policy_reason": "interpreter_execution", "risk_score": risk, "risk_level": _risk_level(risk)},
        )

    if executable_name in COMMAND_TOOL_POLICY["blocked_commands"]:
        return ExecutionResult(
            success=False,
            message="Command blocked by safety policy.",
            data={"policy_decision": "blocked", "policy_reason": "blocked_command", "risk_score": risk, "risk_level": _risk_level(risk)},
        )

    lowered_args = [token.lower() for token in parts[1:]]
    if any(token in COMMAND_TOOL_POLICY["blocked_arguments"] for token in lowered_args):
        return ExecutionResult(
            success=False,
            message="Command blocked by safety policy.",
            data={"policy_decision": "blocked", "policy_reason": "blocked_argument", "risk_score": risk, "risk_level": _risk_level(risk)},
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

    risk = _risk_score(parts)

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
            data={"policy_decision": "approved", "policy_reason": "allowed_command", "risk_score": risk, "risk_level": _risk_level(risk)},
        )

    return ExecutionResult(
        success=True,
        message=output.strip() or "Command executed successfully.",
        data={"policy_decision": "approved", "policy_reason": "allowed_command", "risk_score": risk, "risk_level": _risk_level(risk)},
    )
