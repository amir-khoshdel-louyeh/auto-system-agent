import queue
import shlex
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from auto_system_agent.command_guard import assess_command, check_command
from auto_system_agent.models import ExecutionResult, PlannedTask
from auto_system_agent.terminal import TerminalSession

_VERDICT_TO_LEVEL = {"ALLOW": "low", "CONFIRM": "medium", "DENY": "high"}

#: P3.2 per-step timeouts: default 300s, privilege wrappers 120s.
DEFAULT_STEP_TIMEOUT = 300
PRIVILEGED_STEP_TIMEOUT = 120
_PRIVILEGE_WRAPPERS = {"sudo", "su", "doas", "runas"}

#: P3.2 concurrency bounds: 4 workers, 50 queued steps of back-pressure.
STEP_MAX_WORKERS = 4
STEP_QUEUE_SIZE = 50

#: P3.3 compensation map: creating commands and their undo commands.
#: mkdir X -> rmdir X, touch X -> rm X, apt install pkg -> apt remove -y pkg,
#: zip archive -> rm archive (plus dnf/pacman/zypper/apk analogues).
_INSTALL_REMOVE_PAIRS = (
    ("apt", "install", "remove -y"),
    ("dnf", "install", "remove -y"),
    ("zypper", "install", "remove -y"),
    ("apk", "add", "del"),
    ("pacman", "-S", "-Rns"),
)


def compensation_for(command: str) -> str | None:
    """Undo command for one simple creating/installing command, else None."""
    from auto_system_agent.command_guard import CommandNode, parse_command

    try:
        node = parse_command((command or "").strip())
    except Exception:
        return None
    # Unwrap one redirection layer; anything compound has no rollback.
    target_node = node
    seen_redir = False
    while not isinstance(target_node, CommandNode):
        inner = getattr(target_node, "cmd", None)
        if inner is None or seen_redir:
            return None
        seen_redir = True
        target_node = inner
    argv = list(target_node.argv)
    if not argv:
        return None
    sudo_prefix: list[str] = []
    if Path(argv[0]).name.lower() in _PRIVILEGE_WRAPPERS and len(argv) > 1:
        sudo_prefix = [argv[0]]
        argv = argv[1:]
    if not argv:
        return None
    prog = Path(argv[0]).name.lower()
    args = argv[1:]

    def _last_path(candidates: list[str]) -> str | None:
        paths = [c for c in candidates if c and not c.startswith("-")]
        return paths[-1] if paths else None

    if prog == "mkdir":
        target = _last_path(args)
        return shlex.join(sudo_prefix + ["rmdir", target]) if target else None
    if prog == "touch":
        target = _last_path(args)
        return shlex.join(sudo_prefix + ["rm", target]) if target else None
    if prog == "zip":
        paths = [c for c in args if c and not c.startswith("-")]
        # The archive is the first path argument (`zip -r demo.zip demo`).
        if paths:
            return shlex.join(sudo_prefix + ["rm", paths[0]])
        return None
    for manager, install_verb, remove_verb in _INSTALL_REMOVE_PAIRS:
        lowered = [a.lower() for a in args]
        if prog == manager and install_verb.lower() in lowered:
            pkgs = [a for a in args if a and not a.startswith("-") and a.lower() != install_verb.lower()]
            if not pkgs:
                return None
            return shlex.join(sudo_prefix + [argv[0]] + remove_verb.split() + pkgs)
    return None


def step_timeout(command: str) -> int:
    """Timeout for one step: 120s behind sudo/su, 300s otherwise."""
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        return DEFAULT_STEP_TIMEOUT
    if parts and Path(parts[0]).name.lower() in _PRIVILEGE_WRAPPERS:
        return PRIVILEGED_STEP_TIMEOUT
    return DEFAULT_STEP_TIMEOUT


class SafeExecutor:
    """Direct terminal executor - all requests run as bash commands."""

    def __init__(
        self,
        terminal: TerminalSession | None = None,
        max_workers: int = STEP_MAX_WORKERS,
        queue_size: int = STEP_QUEUE_SIZE,
    ) -> None:
        self._terminal = terminal or TerminalSession()
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="safe-exec")
        self._slots = threading.Semaphore(max(1, queue_size))
        self._queue_size = max(1, queue_size)
        self._depth = 0
        self._depth_lock = threading.Lock()
        # P3.3 WAL journal: {seq, cmd, compensation, success, exit_code}.
        self._journal: list[dict] = []
        self._journal_lock = threading.Lock()

    @property
    def journal(self) -> list[dict]:
        """Copy of the transaction journal (oldest first)."""
        with self._journal_lock:
            return [dict(entry) for entry in self._journal]

    def _journal_append(self, command: str) -> dict:
        entry = {
            "seq": 0,
            "cmd": command,
            "compensation": compensation_for(command),
            "success": None,
            "exit_code": None,
            "timed_out": False,
            "compensated": False,
        }
        with self._journal_lock:
            entry["seq"] = len(self._journal)
            self._journal.append(entry)
        return entry

    @staticmethod
    def _journal_close(entry: dict, result: ExecutionResult) -> None:
        entry["success"] = bool(result.success)
        try:
            entry["exit_code"] = result.data.get("exit_code")
        except Exception:
            entry["exit_code"] = None
        entry["timed_out"] = "timed out after" in (result.message or "")

    def compensate(self, since: int = 0) -> list[ExecutionResult]:
        """Undo journaled work in reverse: successful or timed-out entries."""
        with self._journal_lock:
            pending = [
                entry
                for entry in self._journal
                if entry["seq"] >= since
                and entry["compensation"]
                and not entry["compensated"]
                and (entry["success"] or entry["timed_out"])
            ]
        results: list[ExecutionResult] = []
        for entry in reversed(pending):
            try:
                result = self._terminal.run(
                    entry["compensation"], timeout=step_timeout(entry["compensation"])
                )
            except Exception as exc:
                result = ExecutionResult(success=False, message=f"Compensation failed: {exc}")
            entry["compensated"] = True
            results.append(result)
        return results

    def queue_full(self) -> bool:
        """Back-pressure signal: True while 50 steps are already queued."""
        with self._depth_lock:
            return self._depth >= self._queue_size

    def pending_depth(self) -> int:
        """Queued/in-flight step count for metrics sampling."""
        with self._depth_lock:
            return self._depth

    def submit(self, tool_key: str, task: PlannedTask) -> Future:
        """Queue one step; blocks when full so planners pause while we drain."""
        self._slots.acquire()
        with self._depth_lock:
            self._depth += 1

        def _release(future: Future) -> None:
            with self._depth_lock:
                self._depth -= 1
            self._slots.release()

        future = self._pool.submit(self.execute, tool_key, task)
        future.add_done_callback(_release)
        return future

    def close(self) -> None:
        """Drain queued steps and release pool threads."""
        self._pool.shutdown(wait=True)

    @property
    def terminal(self) -> TerminalSession:
        return self._terminal

    # Kept for backward compat with tests that access _working_directory
    @property
    def _working_directory(self):
        return self._terminal.cwd

    @_working_directory.setter
    def _working_directory(self, value):
        # Allow tests to set working directory
        from pathlib import Path
        try:
            p = Path(value).resolve()
            if p.exists() and p.is_dir():
                self._terminal._cwd = p
        except Exception:
            pass

    def execute(self, tool_key: str, task: PlannedTask) -> ExecutionResult:
        if tool_key == "help":
            return ExecutionResult(
                success=True,
                message=(
                    "Terminal mode: all requests are executed as bash commands via `bash -lc`. "
                    "Examples: touch ~/Downloads/test.py, mkdir -p ~/Downloads/demo, ls -la ~/Downloads, "
                    "cat ~/Downloads/test.py, cp ~/Downloads/a.txt ~/Downloads/b.txt, rm ~/Downloads/test.py, "
                    "zip -r ~/Downloads/archive.zip ~/Downloads/demo, sudo apt install -y vlc"
                ),
            )

        if tool_key == "run_command":
            command = (task.target or "").strip()
            if not command:
                # Fallback to raw_input if target empty but raw_input is a command
                command = (task.raw_input or "").strip()
            if not command:
                return ExecutionResult(success=False, message="No command provided.")
            guard_message = check_command(command, cwd=self._terminal.cwd)
            if guard_message:
                try:
                    preflight_assessment = assess_command(command)
                except Exception:
                    preflight_assessment = None
                data: dict = {
                    "command": command,
                    "guard": "preflight",
                    "policy_decision": "blocked",
                    "policy_reason": "preflight",
                }
                if preflight_assessment:
                    data.update(
                        {
                            "risk_score": preflight_assessment["score"],
                            "risk_level": _VERDICT_TO_LEVEL.get(
                                preflight_assessment["verdict"], "high"
                            ),
                            "verdict": preflight_assessment["verdict"],
                            "canonical_form": preflight_assessment["canonical_form"],
                        }
                    )
                return ExecutionResult(success=False, message=guard_message, data=data)
            assessment = assess_command(command)
            if assessment["verdict"] == "DENY":
                reasons = "; ".join(assessment["reasons"]) or "deny-list"
                return ExecutionResult(
                    success=False,
                    message=(
                        f"Blocked by safety policy ({assessment['score']}/100): {reasons}. "
                        f"Canonical: {assessment['canonical_form'] or command}"
                    ),
                    data={
                        "command": command,
                        "guard": "policy",
                        "policy_decision": "blocked",
                        "policy_reason": assessment["reasons"][0] if assessment["reasons"] else "deny-list",
                        "risk_score": assessment["score"],
                        "risk_level": _VERDICT_TO_LEVEL["DENY"],
                        "verdict": "DENY",
                        "canonical_form": assessment["canonical_form"],
                    },
                )
            entry = self._journal_append(command)
            result = self._terminal.run(command, timeout=step_timeout(command))
            self._journal_close(entry, result)
            if entry["timed_out"]:
                # Killed mid-work: undo partial journal before reporting.
                already = {item["seq"] for item in self.journal if item["compensated"]}
                self.compensate()
                undone = [
                    item["compensation"]
                    for item in self.journal
                    if item["compensated"] and item["seq"] not in already
                ]
                try:
                    result.data["compensated"] = undone
                except Exception:
                    pass
            policy_data = {
                "command": command,
                "policy_decision": "approved",
                "policy_reason": "; ".join(assessment["reasons"]) or "allowed_command",
                "risk_score": assessment["score"],
                "risk_level": _VERDICT_TO_LEVEL.get(assessment["verdict"], "low"),
                "verdict": assessment["verdict"],
                "canonical_form": assessment["canonical_form"],
            }
            try:
                result.data.update({k: v for k, v in policy_data.items() if k not in result.data})
            except Exception:
                pass
            return result

        # No legacy bypass: every shell path above carries a verdict first.
        # Unknown tool keys never reach the terminal.
        return ExecutionResult(
            success=False,
            message=f"Unsupported tool: {tool_key}",
            data={"policy_decision": "blocked", "policy_reason": "unsupported_tool"},
        )
