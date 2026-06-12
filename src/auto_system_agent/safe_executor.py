from auto_system_agent.command_guard import assess_command, check_command
from auto_system_agent.models import ExecutionResult, PlannedTask
from auto_system_agent.terminal import TerminalSession

_VERDICT_TO_LEVEL = {"ALLOW": "low", "CONFIRM": "medium", "DENY": "high"}


class SafeExecutor:
    """Direct terminal executor - all requests run as bash commands."""

    def __init__(self, terminal: TerminalSession | None = None) -> None:
        self._terminal = terminal or TerminalSession()

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
                return ExecutionResult(
                    success=False,
                    message=guard_message,
                    data={
                        "command": command,
                        "guard": "preflight",
                        "policy_decision": "blocked",
                        "policy_reason": "preflight",
                    },
                )
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
            result = self._terminal.run(command)
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
