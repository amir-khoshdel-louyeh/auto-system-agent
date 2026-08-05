"""Shell worker: serves `steps:shell` — gate, then real sandboxed execution.

Defense in depth: the worker re-runs the policy gate even though the
dispatcher scheduler already gated the node, and then executes the command
**inside the Phase 8 sandbox** rather than in this process (item [99]):

- the command runs under ``/bin/sh`` in a namespace whose only writable mount
  is the run's workspace, with no network and bounded wall clock / file size;
- the workspace is either declared by the dispatcher (validated against the
  allowed roots, never trusted blindly) or a temporary directory this worker
  owns and removes;
- a non-zero exit, a timeout or an isolation violation fails the step with
  the sandbox's own reason, so a refused run can never look like success;
- the persisted output is deterministic (no durations), because a replay
  compares step summaries.

Without OS isolation on the host a command request is refused by the sandbox
instead of running unguarded — the step fails with that reason (fail closed).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from engine.executor.gate import ALLOW, decide
from sandbox.commands import clamp_timeout, run_command
from sandbox.contract import DEFAULT_TIMEOUT_S, is_safe_relative

from ..backend import Backend
from ..consumer import Runner
from .core import ApprovalRequired, PolicyDenied, make_step_worker

#: Directory that may contain run workspaces; unset means "temporary only".
WORKSPACE_ROOT_ENV = "ASA_WORKSPACE_ROOT"


def workspace_root() -> Path | None:
    """The configured run-workspace root, or None when unset/blank."""
    raw = os.environ.get(WORKSPACE_ROOT_ENV, "").strip()
    return Path(raw) if raw else None


def _permitted_roots() -> tuple[Path, ...]:
    """Roots a *declared* workspace may live under.

    A payload that names its own workspace is untrusted input: it may only
    point inside the configured run-workspace root or inside the system temp
    directory (where the dispatcher puts throwaway runs). Anything else is
    refused rather than written to.
    """
    roots: list[Path] = []
    configured = workspace_root()
    if configured is not None:
        roots.append(configured.resolve())
    roots.append(Path(tempfile.gettempdir()).resolve())
    return tuple(dict.fromkeys(roots))


def declared_workspace(value: object) -> Path | None:
    """Validate a workspace path handed over by the dispatcher (fail closed)."""
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise RuntimeError(f"declared workspace {text!r} must be an absolute path")
    resolved = candidate.resolve()
    for root in _permitted_roots():
        if root in resolved.parents:
            return resolved
    where = ", ".join(str(root) for root in _permitted_roots())
    raise RuntimeError(f"declared workspace {text!r} is not inside {where}")


def resolve_workspace(params: dict[str, Any], ctx: dict[str, Any]) -> tuple[Path, str, bool]:
    """Return ``(workspace, mode, temporary)`` for one shell node.

    Order: an in-process caller's explicit ``ctx["workspace"]``, then the
    dispatcher's declared workspace in the payload, then a temporary directory
    this worker owns (``temporary=True`` means the caller must remove it).
    """
    explicit = ctx.get("workspace")
    if explicit:
        return Path(explicit), "run", False
    data = ctx.get("data") if isinstance(ctx.get("data"), dict) else {}
    declared = declared_workspace(data.get("workspace") if data else None)
    if declared is not None:
        return declared, "run", False
    return Path(tempfile.mkdtemp(prefix="shell-workspace-")), "temporary", True


def _node_params(node: dict[str, Any]) -> dict[str, Any]:
    """Compiled nodes nest params (`{"type": .., "params": {"run": ..}}`)."""
    params = node.get("params")
    return params if isinstance(params, dict) else {}


async def execute_shell(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Gate, then run the command in the sandbox (or delegate to `exec_fn`)."""
    params = _node_params(node)
    command = str(params.get("run", ""))
    verdict = await decide(node_id, {"type": "shell", "run": command})
    if verdict == "DENY":
        raise PolicyDenied(f"policy DENY for shell node '{node_id}'")
    if verdict != ALLOW:
        raise ApprovalRequired(f"shell node '{node_id}' needs approval (phase 6)")
    exec_fn = ctx.get("exec_fn")
    if exec_fn is not None:
        return await exec_fn(node_id, node, ctx)
    if not command.strip():
        raise RuntimeError(f"shell node '{node_id}' has no command to run")

    workspace, mode, temporary = resolve_workspace(params, ctx)
    workdir = str(params.get("workdir", "") or "").strip()
    if workdir:
        if not is_safe_relative(workdir):
            if temporary:
                shutil.rmtree(workspace, ignore_errors=True)
            raise RuntimeError(f"shell node '{node_id}': workdir {workdir!r} must be relative")
        workspace = workspace / workdir
    timeout_s = clamp_timeout(params.get("timeout_s", DEFAULT_TIMEOUT_S))
    try:
        result = await asyncio.to_thread(
            run_command, command, workspace=workspace, timeout_s=timeout_s
        )
    finally:
        if temporary:
            shutil.rmtree(workspace, ignore_errors=True)
    if not result.ok:
        raise RuntimeError(f"shell node '{node_id}' {result.output_summary}")
    # Deterministic on purpose: a replay compares these summaries (FR-13), so
    # no durations or timestamps may appear here.
    return {
        "ran": command,
        "mode": f"sandboxed ({result.isolation})",
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "workspace": mode,
        "files": [change.as_dict() for change in result.files],
    }


def make_shell_worker(backend: Backend, group: str, exec_fn=None) -> Runner:
    """Runner over `steps:shell`."""

    async def run(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await execute_shell(node_id, node, {**ctx, "exec_fn": exec_fn})

    return make_step_worker(backend, group, "shell", run)
