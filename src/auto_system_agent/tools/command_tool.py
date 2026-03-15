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

BLOCKED_SEPARATORS = {"&&", "||", ";", "|"}


def run_command(command_text: str) -> ExecutionResult:
    if not command_text.strip():
        return ExecutionResult(success=False, message="No command provided.")

    parts = shlex.split(command_text)
    if any(token in BLOCKED_SEPARATORS for token in parts):
        return ExecutionResult(success=False, message="Command chaining is blocked by safety policy.")

    executable_name = Path(parts[0]).name
    if executable_name in BLOCKED_TOKENS:
        return ExecutionResult(success=False, message="Command blocked by safety policy.")

    if any(token in BLOCKED_TOKENS for token in parts):
        return ExecutionResult(success=False, message="Command blocked by safety policy.")

    completed = subprocess.run(parts, capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")

    if completed.returncode != 0:
        return ExecutionResult(
            success=False,
            message=f"Command failed with code {completed.returncode}.\n{output.strip()}",
        )

    return ExecutionResult(success=True, message=output.strip() or "Command executed successfully.")
