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


def _check_command_policy(parts: list[str]) -> ExecutionResult | None:
    if any(token in COMMAND_TOOL_POLICY["blocked_separators"] for token in parts):
        return ExecutionResult(success=False, message="Command chaining is blocked by safety policy.")

    executable_name = Path(parts[0]).name.lower()
    if executable_name in COMMAND_TOOL_POLICY["blocked_interpreters"]:
        return ExecutionResult(success=False, message="Interpreter and shell execution is blocked by safety policy.")

    if executable_name in COMMAND_TOOL_POLICY["blocked_commands"]:
        return ExecutionResult(success=False, message="Command blocked by safety policy.")

    lowered_args = [token.lower() for token in parts[1:]]
    if any(token in COMMAND_TOOL_POLICY["blocked_arguments"] for token in lowered_args):
        return ExecutionResult(success=False, message="Command blocked by safety policy.")

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
        )

    return ExecutionResult(success=True, message=output.strip() or "Command executed successfully.")
