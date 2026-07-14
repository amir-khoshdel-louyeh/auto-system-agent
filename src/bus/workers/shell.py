"""Shell worker: serves `steps:shell` (gate + record-only exec, MVP).

Defense in depth: the worker re-runs the policy gate even though the
dispatcher scheduler already gated the node. Real bash execution is deferred
to the phase 6 sandbox; until then the worker records the command it *would*
run (injected `exec_fn` overrides this in tests).
"""

from __future__ import annotations

from typing import Any

from engine.executor.gate import decide

from ..backend import Backend
from ..consumer import Runner
from .core import PolicyDenied, make_step_worker


async def execute_shell(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Gate, then record (or delegate to the injected `exec_fn`)."""
    from engine.executor.gate import ALLOW

    verdict = await decide(node_id, node)
    if verdict == "DENY":
        raise PolicyDenied(f"policy DENY for shell node '{node_id}'")
    if verdict != ALLOW:
        from .core import ApprovalRequired

        raise ApprovalRequired(f"shell node '{node_id}' needs approval (phase 6)")
    exec_fn = ctx.get("exec_fn")
    if exec_fn is not None:
        return await exec_fn(node_id, node, ctx)
    return {"ran": node.get("params", {}).get("run", ""),
            "mode": "record-only (real bash: phase 6 sandbox)"}


def make_shell_worker(backend: Backend, group: str, exec_fn=None) -> Runner:
    """Runner over `steps:shell`."""

    async def run(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await execute_shell(node_id, node, {**ctx, "exec_fn": exec_fn})

    return make_step_worker(backend, group, "shell", run)
