import subprocess
import os

from auto_system_agent.models import ExecutionResult, PlannedTask
from auto_system_agent.tools.command_tool import run_command
from auto_system_agent.tools.file_tool import (
    compress_path,
    create_folder,
    delete_path,
    list_files,
    move_path,
)
from auto_system_agent.tools.install_tool import build_install_command, verify_install_environment


def _is_transient_install_failure(output_text: str) -> bool:
    lowered = output_text.lower()
    transient_markers = {
        "temporary failure",
        "timed out",
        "timeout",
        "try again",
        "connection reset",
        "connection refused",
        "network is unreachable",
        "could not resolve",
        "failed to fetch",
    }
    return any(marker in lowered for marker in transient_markers)


class SafeExecutor:
    """Executes only predefined safe operations."""

    def execute(self, tool_key: str, task: PlannedTask) -> ExecutionResult:
        if tool_key == "install_app":
            result = build_install_command(task.target or "")
            if not result.success:
                return result

            command = result.data["command"]
            env_check = verify_install_environment(command)
            if not env_check.success:
                return env_check

            retries = int(os.getenv("AUTO_AGENT_INSTALL_RETRIES", "2") or "2")
            attempts = max(1, retries + 1)
            last_failure = ""
            for attempt in range(1, attempts + 1):
                try:
                    completed = subprocess.run(command, capture_output=True, text=True, check=False)
                except FileNotFoundError:
                    return ExecutionResult(
                        success=False,
                        message=f"Install command not found on this system: {command[0]}",
                    )
                except PermissionError:
                    return ExecutionResult(
                        success=False,
                        message=f"Permission denied while running install command: {command[0]}",
                    )
                except OSError as exc:
                    if attempt < attempts:
                        last_failure = str(exc)
                        continue
                    return ExecutionResult(
                        success=False,
                        message=f"Could not run install command after retries: {exc}",
                    )

                output = (completed.stdout or "") + (completed.stderr or "")
                if completed.returncode == 0:
                    if attempt > 1:
                        return ExecutionResult(success=True, message=f"Installation finished after retry #{attempt}.\n{output.strip()}")
                    return ExecutionResult(success=True, message=output.strip() or "Installation finished.")

                last_failure = f"code {completed.returncode}: {output.strip()}"
                if attempt < attempts and _is_transient_install_failure(output):
                    continue
                return ExecutionResult(
                    success=False,
                    message=f"Installation failed with code {completed.returncode}.\n{output.strip()}",
                )

            return ExecutionResult(success=False, message=f"Installation failed after retries. Last error: {last_failure}")

        if tool_key == "create_folder":
            return create_folder(task.target or "")

        if tool_key == "compress":
            return compress_path(task.target or "")

        if tool_key == "list_files":
            return list_files(task.target or ".")

        if tool_key == "move_path":
            return move_path(task.target or "", task.options.get("destination", ""))

        if tool_key == "delete_path":
            return delete_path(task.target or "")

        if tool_key == "run_command":
            return run_command(task.target or "")

        if tool_key == "help":
            return ExecutionResult(
                success=True,
                message=(
                    "Try commands like: install vlc, create folder demo, compress demo, "
                    "move demo.txt to archive/demo.txt, delete archive/demo.txt, "
                    "list files in ., run pwd, or direct shell commands like ls -la."
                ),
            )

        return ExecutionResult(success=False, message="Could not map request to a supported tool.")
