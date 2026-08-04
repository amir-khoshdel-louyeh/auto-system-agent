"""Phase 5 items [49],[50]: audit query and execution timeline endpoints.

- `GET /executions/{id}/audit`  — one page of the append-only audit chain
  (with the total, so clients can paginate) for an execution.
- `GET /executions/{id}/timeline` — the durable execution and its ordered
  step transitions, the run timeline the UI/auditor reads.

Both are read-only and require `audit:read`; an unknown execution is a 404.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from storage.audit import AuditStore
from storage.run_metrics import summarize

from .approvals import ApprovalService
from .deps import CurrentUser, require_permission
from .models import ExecutionStore
from .roles import Permission
from .settings import Settings, get_settings

router = APIRouter(prefix="/executions", tags=["Audit"])
metrics_router = APIRouter(prefix="/metrics", tags=["Metrics"])


def _audit_store(settings: Settings) -> AuditStore:
    from .db import init_db

    return AuditStore(init_db(settings.sqlite_path))


@router.get("")
def list_executions(
    limit: int = Query(50, ge=1, le=500),
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> dict:
    """Newest executions first — the run list the client shows (item [77])."""
    from .db import init_db

    rows = ExecutionStore(init_db(settings.sqlite_path)).list_executions(limit=limit)
    return {
        "total": len(rows),
        "executions": [
            {
                "id": row["id"],
                "workflow_id": row["workflow_id"],
                "version": row["version"],
                "status": row["status"],
                "triggered_by": row["triggered_by"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": row["error"],
            }
            for row in rows
        ],
    }


@router.get("/{execution_id}/audit")
def query_audit(
    execution_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    """One page of the frozen audit chain for an execution."""
    store = _audit_store(settings)
    events = store.list_page(execution_id=execution_id, limit=limit, offset=offset)
    return {
        "execution_id": execution_id,
        "total": store.count(execution_id),
        "limit": limit,
        "offset": offset,
        "events": events,
    }


@router.get("/{execution_id}/audit/verify")
def verify_audit(
    execution_id: str,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    """Verify one audit chain and name the first broken link (FR-12, item [54])."""
    result = _audit_store(settings).verify_chain(execution_id)
    return {
        "execution_id": execution_id,
        "ok": result.ok,
        "checked": result.checked,
        "first_broken_seq": result.first_broken_seq,
        "reason": result.reason,
    }


@router.get("/{execution_id}/timeline")
def execution_timeline(
    execution_id: str,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> dict:
    """The durable execution row plus its ordered step transitions."""
    from .db import init_db

    store = ExecutionStore(init_db(settings.sqlite_path))
    try:
        execution = store.get(execution_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown execution '{execution_id}'"
        ) from exc
    steps = [
        {
            "node_id": step["node_id"],
            "state": step["state"],
            "attempt": step["attempt"],
            "error": step["error"],
            "updated_at": step["updated_at"],
        }
        for step in execution["steps"]
    ]
    return {
        "execution_id": execution_id,
        "workflow_id": execution["workflow_id"],
        "version": execution["version"],
        "status": execution["status"],
        "triggered_by": execution["triggered_by"],
        "started_at": execution["started_at"],
        "finished_at": execution["finished_at"],
        "error": execution["error"],
        "steps": steps,
    }


@metrics_router.get("/executions")
def execution_metrics(
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    """Aggregate success/latency/retry/approval metrics (item [51], FR-16)."""
    from .db import init_db

    path = init_db(settings.sqlite_path)
    stores = ExecutionStore(path)
    approvals = ApprovalService(path)
    return summarize(
        stores.list_executions(),
        steps=stores.list_all_steps(),
        requests=[asdict(req) for req in approvals.list_requests()],
        decisions=approvals.all_decisions(),
    ).as_dict()
