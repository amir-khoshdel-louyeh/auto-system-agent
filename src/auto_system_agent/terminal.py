import subprocess
import shlex
import os
import threading
from pathlib import Path
from typing import Optional

from auto_system_agent.models import ExecutionResult


class TerminalSession:
    """Persistent bash instance for direct terminal execution.

    All user requests are translated to shell commands and run here.
    No per-request python tool is needed - the shell is the tool.
    """

    def __init__(self, initial_cwd: Path | None = None) -> None:
        self._cwd = (initial_cwd or Path.home()).resolve() if (initial_cwd or Path.home()).exists() else Path.home().resolve()
        if not self._cwd.exists():
            self._cwd = Path.cwd().resolve()
        self._history: list[str] = []
        self._lock = threading.Lock()

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def _record(self, cmd: str) -> None:
        self._history.append(cmd)
        if len(self._history) > 500:
            self._history = self._history[-500:]

    def _handle_cd(self, target: str) -> ExecutionResult:
        # cd without args -> home
        dest = target.strip() if target.strip() else "~"
        if dest == "~":
            new_path = Path.home().resolve()
        else:
            # Expand ~ and resolve relative to current cwd
            new_path = (self._cwd / dest).expanduser().resolve()
            # For paths like ~/Downloads, expanduser already resolves
            if dest.startswith("~"):
                new_path = Path(dest).expanduser().resolve()
            elif dest.startswith("/"):
                new_path = Path(dest).expanduser().resolve()

        if not new_path.exists() or not new_path.is_dir():
            return ExecutionResult(success=False, message=f"cd: no such directory: {target}")

        self._cwd = new_path
        return ExecutionResult(success=True, message=str(self._cwd))

    def _is_cd_command(self, cmd: str) -> Optional[str]:
        try:
            parts = shlex.split(cmd.strip())
        except ValueError:
            return None
        if not parts:
            return None
        if parts[0] != "cd":
            return None
        # cd with no args or one arg
        if len(parts) == 1:
            return ""
        if len(parts) == 2:
            return parts[1]
        # cd with too many args -> treat as error, but let shell handle
        return None

    def run(self, command: str, timeout: int = 30) -> ExecutionResult:
        cmd = command.strip()
        if not cmd:
            return ExecutionResult(success=False, message="No command provided.")

        with self._lock:
            self._record(cmd)

            # Handle special built-ins that need cwd tracking
            if cmd.strip() == "pwd":
                return ExecutionResult(success=True, message=str(self._cwd))

            if cmd.strip() == "history":
                if not self._history:
                    return ExecutionResult(success=True, message="(no history)")
                lines = [f"{i}: {c}" for i, c in enumerate(self._history, 1)]
                return ExecutionResult(success=True, message="\n".join(lines))

            if cmd.strip() == "clear":
                return ExecutionResult(success=True, message="Terminal cleared.")

            if cmd.strip() == "exit":
                self._history.clear()
                self._cwd = Path.home().resolve()
                return ExecutionResult(success=True, message="Terminal session closed.")

            cd_target = self._is_cd_command(cmd)
            if cd_target is not None:
                return self._handle_cd(cd_target)

            # For all other commands, run via bash -lc with current cwd
            # Use bash -lc to support pipes, redirects, etc. This is the terminal instance.
            try:
                result = subprocess.run(
                    ["bash", "-lc", cmd],
                    cwd=str(self._cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except FileNotFoundError:
                return ExecutionResult(success=False, message="bash not found on this system.")
            except subprocess.TimeoutExpired:
                return ExecutionResult(success=False, message=f"Command timed out after {timeout}s: {cmd}")
            except OSError as exc:
                return ExecutionResult(success=False, message=f"Could not execute command: {exc}")

            output = (result.stdout or "") + (result.stderr or "")
            output = output.strip()

            # Update cwd if command changed directory via side effect? For commands like `mkdir -p foo && cd foo`
            # We don't auto-track, user should use explicit `cd`. If they do `cd` via ; chain, the shell's cd is lost.
            # Advise planner to use separate `cd` tasks.

            if result.returncode != 0:
                # Keep risk info for agent
                return ExecutionResult(
                    success=False,
                    message=output or f"Command failed with exit code {result.returncode}: {cmd}",
                    data={"exit_code": result.returncode, "command": cmd},
                )

            return ExecutionResult(
                success=True,
                message=output or f"Command executed: {cmd}",
                data={"exit_code": 0, "command": cmd},
            )

    def reset(self) -> None:
        with self._lock:
            self._cwd = Path.home().resolve()
            self._history.clear()
