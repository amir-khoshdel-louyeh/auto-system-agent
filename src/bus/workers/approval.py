"""Approval worker: serves `steps:approval` (request + stub decision, MVP).

Publishes the frozen `approval.requested` contract event, writes the
decision row when a Recorder is provided, and decides via an explicit stub:
quorum == 1 approves, anything higher parks for the human quorum that
arrives with the phase 6 approval worker. The stub never silently approves
custom/gui escalations — those node types never reach this worker.
"""

from __future__ import annotations

from typing import Any

from ..backend import Backend
from ..consumer import Runner
from ..events import new_event
from .core import ApprovalRequired, make_step_worker

APPROVALS_STREAM = "approvals"
STUB_APPROVER = "stub-approver (phase 6: human quorum)"


async def execute_approval(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Request, record, then stub-decide one approval node."""
    backend: Backend = ctx["backend"]
    run_id: str = ctx["run_id"]
    quorum = node.get("params", {}).get("quorum", 1)
    await backend.publish(
        APPROVALS_STREAM,
        new_event("approval.requested",
                  {"run_id": run_id, "node_id": node_id, "quorum": quorum},
                  correlation_id=run_id,
                  idempotency_key=f"{run_id}:{node_id}:approval"),
    )
    recorder = ctx.get("recorder")
    execution_id = ctx.get("execution_id", "")
    if quorum == 1:
        if recorder is not None and execution_id:
            recorder.add_approval(execution_id, node_id, "approved",
                                  decided_by=STUB_APPROVER, reason="quorum 1")
        return {"decision": "approved", "decided_by": STUB_APPROVER}
    if recorder is not None and execution_id:
        recorder.add_approval(execution_id, node_id, "pending",
                              decided_by="", reason="needs human quorum (phase 6)")
    raise ApprovalRequired(f"approval node '{node_id}': needs human quorum (phase 6)")


def make_approval_worker(backend: Backend, group: str, ctx_extra: dict[str, Any] | None = None) -> Runner:
    """Runner over `steps:approval` (extra ctx merged per task)."""

    async def run(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        return await execute_approval(node_id, node, {**(ctx_extra or {}), **ctx})

    return make_step_worker(backend, group, "approval", run)
