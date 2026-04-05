import subprocess
import os
import shlex
from pathlib import Path
from urllib import error, parse, request

from auto_system_agent.models import ExecutionResult, PlannedTask
from auto_system_agent.tools.command_tool import COMMAND_TOOL_POLICY, run_command
from auto_system_agent.tools.file_tool import (
    compress_path,
    copy_path,
    create_empty_file,
    create_folder,
    delete_path,
    find_files_by_name,
    grep_in_file,
    list_files,
    make_executable,
    move_path,
    view_file,
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

    def __init__(self) -> None:
        self._working_directory = Path.cwd()
        self._command_history: list[str] = []

    def _check_sudo_policy(self, inner_parts: list[str]) -> ExecutionResult | None:
        if not inner_parts:
            return ExecutionResult(success=False, message="sudo requires a command.")

        if any(token in COMMAND_TOOL_POLICY["blocked_separators"] for token in inner_parts):
            return ExecutionResult(success=False, message="Command chaining is blocked by safety policy.")

        executable_name = Path(inner_parts[0]).name.lower()
        if executable_name in COMMAND_TOOL_POLICY["blocked_interpreters"]:
            return ExecutionResult(success=False, message="Interpreter and shell execution is blocked by safety policy.")
        if executable_name in COMMAND_TOOL_POLICY["blocked_commands"]:
            return ExecutionResult(success=False, message="Command blocked by safety policy.")

        lowered_args = [token.lower() for token in inner_parts[1:]]
        if any(token in COMMAND_TOOL_POLICY["blocked_arguments"] for token in lowered_args):
            return ExecutionResult(success=False, message="Command blocked by safety policy.")

        return None

    def _handle_navigation_command(self, command_text: str) -> ExecutionResult | None:
        text = command_text.strip()
        if not text:
            return None

        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return ExecutionResult(success=False, message=f"Invalid command syntax: {exc}")

        if not parts:
            return None

        self._command_history.append(text)
        if len(self._command_history) > 200:
            self._command_history = self._command_history[-200:]

        if parts[0] == "clear":
            return ExecutionResult(success=True, message="Terminal cleared.")

        if parts[0] == "history":
            if not self._command_history:
                return ExecutionResult(success=True, message="(no history)")
            lines = [f"{idx}: {cmd}" for idx, cmd in enumerate(self._command_history, start=1)]
            return ExecutionResult(success=True, message="\n".join(lines))

        if parts[0] == "exit":
            self._working_directory = Path.cwd()
            self._command_history = []
            return ExecutionResult(success=True, message="Terminal session closed.")

        if parts[0] == "top":
            try:
                completed = subprocess.run(
                    ["ps", "-eo", "pid,comm,%cpu,%mem", "--sort=-%cpu"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as exc:
                return ExecutionResult(success=False, message=f"Could not list processes: {exc}")

            output_lines = (completed.stdout or "").splitlines()
            if not output_lines:
                return ExecutionResult(success=False, message="Could not list processes.")
            preview = "\n".join(output_lines[:16])
            return ExecutionResult(success=True, message=preview)

        if parts[0] == "pwd":
            return ExecutionResult(success=True, message=str(self._working_directory))

        if parts[0] == "ls":
            target = str(self._working_directory)
            if len(parts) > 1:
                target = str((self._working_directory / parts[1]).resolve())
            return list_files(target)

        if parts[0] == "cd":
            destination = parts[1] if len(parts) > 1 else "~"
            if destination == "~":
                target = Path.home().resolve()
            else:
                target = (self._working_directory / destination).expanduser().resolve()

            if not target.exists() or not target.is_dir():
                return ExecutionResult(success=False, message=f"Invalid directory: {target}")

            self._working_directory = target
            return ExecutionResult(success=True, message=f"Current directory: {self._working_directory}")

        if parts[0] == "mkdir":
            if len(parts) < 2:
                return ExecutionResult(success=False, message="mkdir requires a folder name.")
            target = str((self._working_directory / parts[1]).expanduser())
            return create_folder(target)

        if parts[0] == "touch":
            if len(parts) < 2:
                return ExecutionResult(success=False, message="touch requires a file path.")
            target = str((self._working_directory / parts[1]).expanduser())
            return create_empty_file(target)

        if parts[0] == "cp":
            if len(parts) < 3:
                return ExecutionResult(success=False, message="cp requires source and destination.")
            source = str((self._working_directory / parts[1]).expanduser())
            destination = str((self._working_directory / parts[2]).expanduser())
            return copy_path(source, destination)

        if parts[0] == "mv":
            if len(parts) < 3:
                return ExecutionResult(success=False, message="mv requires source and destination.")
            source = str((self._working_directory / parts[1]).expanduser())
            destination = str((self._working_directory / parts[2]).expanduser())
            return move_path(source, destination)

        if parts[0] == "rm":
            if len(parts) < 2:
                return ExecutionResult(success=False, message="rm requires a path.")

            recursive = len(parts) >= 3 and parts[1] == "-r"
            target_arg = parts[2] if recursive else parts[1]
            target = (self._working_directory / target_arg).expanduser().resolve()
            if not target.exists():
                return ExecutionResult(success=False, message=f"Path does not exist: {target}")

            if target.is_dir() and not recursive:
                return ExecutionResult(success=False, message="rm cannot delete a folder without -r.")

            return delete_path(str(target))

        if parts[0] in {"cat", "less", "head", "tail"}:
            if len(parts) < 2:
                return ExecutionResult(success=False, message=f"{parts[0]} requires a file path.")

            target = str((self._working_directory / parts[1]).expanduser())
            if parts[0] == "cat":
                return view_file(target, mode="all")
            if parts[0] == "less":
                return view_file(target, mode="less", line_count=25)
            if parts[0] == "head":
                return view_file(target, mode="head", line_count=10)
            return view_file(target, mode="tail", line_count=10)

        if parts[0] == "chmod":
            if len(parts) < 3 or parts[1] != "+x":
                return ExecutionResult(success=False, message="chmod usage: chmod +x <file>")
            target = str((self._working_directory / parts[2]).expanduser())
            return make_executable(target)

        if parts[0] == "sudo":
            if len(parts) < 2:
                return ExecutionResult(success=False, message="sudo requires a command.")

            inner_parts = parts[1:]
            policy_result = self._check_sudo_policy(inner_parts)
            if policy_result is not None:
                return policy_result

            try:
                completed = subprocess.run(
                    ["sudo", "-n", *inner_parts],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=20,
                    cwd=str(self._working_directory),
                )
            except FileNotFoundError:
                return ExecutionResult(success=False, message="sudo is not available on this system.")
            except subprocess.TimeoutExpired:
                return ExecutionResult(success=False, message="sudo command timed out.")
            except OSError as exc:
                return ExecutionResult(success=False, message=f"Could not execute sudo command: {exc}")

            output = ((completed.stdout or "") + (completed.stderr or "")).strip()
            if completed.returncode != 0:
                return ExecutionResult(success=False, message=f"sudo command failed with code {completed.returncode}.\n{output}")
            return ExecutionResult(success=True, message=output or "sudo command executed successfully.")

        if parts[0] == "grep":
            if len(parts) < 3:
                return ExecutionResult(success=False, message="grep requires search text and file path.")
            search_text = parts[1]
            target = str((self._working_directory / parts[2]).expanduser())
            return grep_in_file(search_text, target)

        if parts[0] == "find":
            if len(parts) < 4 or parts[2] != "-name":
                return ExecutionResult(success=False, message="find usage: find <path> -name <filename>")
            start_path = str((self._working_directory / parts[1]).expanduser())
            filename = parts[3]
            return find_files_by_name(start_path, filename)

        if parts[0] == "ping":
            if len(parts) < 2:
                return ExecutionResult(success=False, message="ping requires a host.")
            host = parts[1]
            if any(ch in host for ch in [";", "&", "|", " "]):
                return ExecutionResult(success=False, message="Invalid ping host.")
            try:
                completed = subprocess.run(
                    ["ping", "-c", "4", "-W", "2", host],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
            except FileNotFoundError:
                return ExecutionResult(success=False, message="ping is not available on this system.")
            except subprocess.TimeoutExpired:
                return ExecutionResult(success=False, message="ping command timed out.")
            except OSError as exc:
                return ExecutionResult(success=False, message=f"Could not execute ping: {exc}")

            output = ((completed.stdout or "") + (completed.stderr or "")).strip()
            if completed.returncode != 0:
                return ExecutionResult(success=False, message=f"ping failed with code {completed.returncode}.\n{output}")
            return ExecutionResult(success=True, message=output)

        if parts[0] == "curl":
            if len(parts) < 2:
                return ExecutionResult(success=False, message="curl requires a URL.")
            url = parts[1].strip()
            parsed = parse.urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return ExecutionResult(success=False, message="curl only supports http/https URLs.")
            try:
                with request.urlopen(url, timeout=10) as response:
                    body = response.read(4096).decode("utf-8", errors="replace")
            except error.URLError as exc:
                return ExecutionResult(success=False, message=f"curl failed: {exc}")
            except TimeoutError:
                return ExecutionResult(success=False, message="curl request timed out.")
            return ExecutionResult(success=True, message=body or "(empty response)")

        return None

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
            command_text = task.target or ""
            navigation_result = self._handle_navigation_command(command_text)
            if navigation_result is not None:
                return navigation_result
            return run_command(command_text)

        if tool_key == "help":
            return ExecutionResult(
                success=True,
                message=(
                    "Try commands like: install vlc, create folder demo, compress demo, "
                    "move demo.txt to archive/demo.txt, delete archive/demo.txt, "
                    "list files in ., run pwd, grep 'text' notes.txt, find . -name notes.txt, ping example.com, curl https://example.com, top, history, or ls -la."
                ),
            )

        return ExecutionResult(success=False, message="Could not map request to a supported tool.")
