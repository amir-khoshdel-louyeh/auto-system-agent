"""Phase 10 item [99]: run a workflow's `shell` command inside the sandbox.

A `shell` node carries a command *string* (`run: setup.sh`). Executing that
string in the worker process would make the whole policy gate decorative, so
the string is executed by ``/bin/sh`` **inside** the sandbox instead: the
workspace is the only writable mount, the network/PID/IPC namespaces are
unshared, the wall clock and the file size are bounded, and the command's
output is truncated to the contract's byte budget.

Two consequences worth stating out loud:

- the shell is a *guest*, not a host process — a `;`/`&&` chain in the YAML
  cannot reach anything outside the workspace it was handed;
- when the host cannot give the sandbox OS isolation (no ``bwrap``/user
  namespaces), a command request is **refused** rather than run unguarded
  (`sandbox.runner._plain_argv`), and the step fails with that reason.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from .contract import (
    DEFAULT_TIMEOUT_S,
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT_S,
    MIN_TIMEOUT_S,
    SandboxRequest,
    SandboxResult,
)
from .runner import bwrap_available, run

#: The interpreter that executes a command *inside* the sandbox.
SHELL = "/bin/sh"


def command_argv(command: str = "", *, argv: Sequence[str] = ()) -> tuple[str, ...]:
    """Build the argv for one shell node.

    An explicit ``argv`` runs directly (no shell); otherwise the command string
    is handed to ``/bin/sh -c`` inside the sandbox. An empty request yields an
    empty argv on purpose, so the contract refuses it instead of running a
    no-op that looks like success.
    """
    if argv:
        return tuple(str(item) for item in argv)
    text = str(command)
    if not text.strip():
        return ()
    return (SHELL, "-c", text)


def command_request(
    command: str = "",
    *,
    argv: Sequence[str] = (),
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> SandboxRequest:
    """A validated-ready `kind="command"` request for one shell node."""
    return SandboxRequest(
        kind="command",
        command=command_argv(command, argv=argv),
        timeout_s=timeout_s,
        max_output_bytes=max_output_bytes,
    )


def clamp_timeout(value: object, default: float = DEFAULT_TIMEOUT_S) -> float:
    """Coerce a node's optional ``timeout_s`` into the contract's bounds."""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if seconds != seconds:  # NaN
        return default
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, seconds))


def isolation_available() -> bool:
    """True when commands can be isolated with OS namespaces on this host."""
    return bwrap_available() and os.name == "posix"


def run_command(
    command: str = "",
    *,
    workspace: str | Path,
    argv: Sequence[str] = (),
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> SandboxResult:
    """Run one shell node's command in the sandbox over ``workspace``.

    Returns the frozen sandbox result; a refusal (no OS isolation), a non-zero
    exit, a timeout or an isolation violation all surface as ``ok=False`` with
    a human-readable ``output_summary`` — the caller decides what a failure
    means for the step.
    """
    request = command_request(
        command,
        argv=argv,
        timeout_s=clamp_timeout(timeout_s),
        max_output_bytes=max_output_bytes,
    )
    request.require_valid()
    return run(request, workspace_dir=workspace)


__all__ = [
    "SHELL",
    "clamp_timeout",
    "command_argv",
    "command_request",
    "isolation_available",
    "run_command",
]
