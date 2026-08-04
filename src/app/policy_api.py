"""Phase 8 item [78]: `POST /policy/explain` (FR-07).

Turns one node spec into the same policy decision the executor would make,
plus the human-readable explanation (headline, why, next step), so a client
can show a person what a verdict means before anything runs. Read-only: it
never persists and never dispatches.

The caller's own role is used, so an explanation can never advertise an
action the caller is not allowed to take.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from safety.explanations import explain
from safety.policy_service import PolicyContext, PolicyService

from .deps import CurrentUser, require_permission
from .roles import Permission

router = APIRouter(prefix="/policy", tags=["Policy"])

#: Frozen node-type vocabulary the explainer accepts.
NODE_TYPES: tuple[str, ...] = ("shell", "gui_click", "gui_type", "approval", "custom")


class ExplainIn(BaseModel):
    type: str = Field(description="node type")
    params: dict[str, Any] = Field(default_factory=dict)
    node_id: str = "node"
    workspace_root: str = ""


@router.post("/explain")
def explain_node(
    body: ExplainIn,
    user: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> dict:
    """Decision + explanation for one node, as this caller would get it."""
    spec = {"type": body.type, **body.params}
    decision = PolicyService().decide(
        body.node_id,
        spec,
        context=PolicyContext(role=user.role.value, workspace_root=body.workspace_root),
    )
    return {"decision": decision.as_dict(), "explanation": explain(decision).as_dict()}
