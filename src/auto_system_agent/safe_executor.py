import subprocess

from auto_system_agent.models import ExecutionResult, PlannedTask
from auto_system_agent.tools.command_tool import run_command
from auto_system_agent.tools.file_tool import (
    compress_path,
    create_folder,
    delete_path,
    list_files,
    move_path,
)
from auto_system_agent.tools.install_tool import build_install_command


class SafeExecutor:
    """Executes only predefined safe operations."""

    def execute(self, tool_key: str, task: PlannedTask) -> ExecutionResult:
        if tool_key == "install_app":
            result = build_install_command(task.target or "")
            if not result.success:
                return result

            command = result.data["command"]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            output = (completed.stdout or "") + (completed.stderr or "")
            if completed.returncode != 0:
                return ExecutionResult(
                    success=False,
                    message=f"Installation failed with code {completed.returncode}.\n{output.strip()}",
                )
            return ExecutionResult(success=True, message=output.strip() or "Installation finished.")

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
