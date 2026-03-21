import shlex
import subprocess
from pathlib import Path

from auto_system_agent.models import ExecutionResult

# Basic deny-list to avoid destructive shell actions in MVP mode.
BLOCKED_TOKENS = {
    "rm",
    "shutdown",
    "reboot",
    "mkfs",
    "dd",
    "poweroff",
}

BLOCKED_EXECUTABLES = {
    "bash",
    "dash",
    "fish",
    "ksh",
    "node",
    "perl",
    "pwsh",
    "powershell",
    "python",
    "python3",
    "ruby",
    "sh",
    "zsh",
}

BLOCKED_ARGUMENTS = {
    "--no-preserve-root",
    "-rf",
    "-fr",
}

BLOCKED_SEPARATORS = {"&&", "||", ";", "|"}


def run_command(command_text: str) -> ExecutionResult:
    if not command_text.strip():
        return ExecutionResult(success=False, message="No command provided.")

    try:
        parts = shlex.split(command_text)
    except ValueError as exc:
        return ExecutionResult(success=False, message=f"Invalid command syntax: {exc}")

    if not parts:
        return ExecutionResult(success=False, message="No command provided.")

    if any(token in BLOCKED_SEPARATORS for token in parts):
        return ExecutionResult(success=False, message="Command chaining is blocked by safety policy.")

    executable_name = Path(parts[0]).name.lower()
    if executable_name in BLOCKED_EXECUTABLES:
        return ExecutionResult(success=False, message="Interpreter and shell execution is blocked by safety policy.")

    if executable_name in BLOCKED_TOKENS:
        return ExecutionResult(success=False, message="Command blocked by safety policy.")

    lowered_parts = [token.lower() for token in parts]
    if any(token in BLOCKED_TOKENS for token in lowered_parts):
        return ExecutionResult(success=False, message="Command blocked by safety policy.")

    if any(token in BLOCKED_ARGUMENTS for token in lowered_parts):
        return ExecutionResult(success=False, message="Command blocked by safety policy.")

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
