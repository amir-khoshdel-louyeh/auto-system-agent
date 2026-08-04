"""Phase 8 item [77]: the approval surface the client needs (FR-08).

The approval state machine lives in :mod:`app.approvals`; this router only
exposes it: list requests (optionally for one execution), read one request
with its decisions, and cast a decision as the authenticated caller.

Authorization is the existing matrix: reading needs ``audit:read``, deciding
needs ``approval:decide``. A denied transition (terminal request, expired
deadline) is a 409 carrying the service's reason, never a silent no-op.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .approvals import ApprovalError, ApprovalService
from .deps import CurrentUser, require_permission
from .roles import Permission
from .settings import Settings, get_settings

router = APIRouter(prefix="/approvals", tags=["Approvals"])


def _service(settings: Settings) -> ApprovalService:
    from .db import init_db

    return ApprovalService(init_db(settings.sqlite_path))


class DecisionIn(BaseModel):
    verdict: Literal["APPROVED", "REJECTED"]
    reason: str = Field(default="", max_length=1000)


@router.get("")
def list_approvals(
    execution_id: str = Query("", max_length=64),
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    """Pending and resolved approval requests, newest last."""
    requests = _service(settings).list_requests(execution_id)
    return {"total": len(requests), "requests": [asdict(request) for request in requests]}


@router.get("/{execution_id}/{node_id}")
def read_approval(
    execution_id: str,
    node_id: str,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    """One request plus the decisions cast so far."""
    service = _service(settings)
    try:
        request = service.get(execution_id, node_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"request": asdict(request), "decisions": service.decisions(execution_id, node_id)}


@router.post("/{execution_id}/{node_id}/decisions")
def decide(
    execution_id: str,
    node_id: str,
    body: DecisionIn,
    user: CurrentUser = Depends(require_permission(Permission.APPROVAL_DECIDE)),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Cast the caller's decision on one request."""
    service = _service(settings)
    try:
        request = service.decide(
            execution_id,
            node_id,
            approver=user.username,
            verdict=body.verdict,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"request": asdict(request)}
