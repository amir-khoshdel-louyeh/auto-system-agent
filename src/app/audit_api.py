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
from fastapi.responses import PlainTextResponse

from storage.audit import AuditStore
from storage.run_metrics import summarize

from .approvals import ApprovalService
from .deps import CurrentUser, require_permission
from .metrics_export import CONTENT_TYPE
from .metrics_export import render as render_prometheus
from .models import ExecutionStore, WorkflowStore
from .roles import Permission
from .settings import Settings, get_settings

router = APIRouter(prefix="/executions", tags=["Audit"])
metrics_router = APIRouter(prefix="/metrics", tags=["Metrics"])


def _audit_store(settings: Settings) -> AuditStore:
    from .db import init_db

    return AuditStore(init_db(settings.sqlite_path))


def _workflow_names(settings: Settings, workflow_ids: list[str]) -> dict[str, str]:
    """Human labels for the run screens: ``workflow_id -> name``."""
    from .db import init_db

    store = WorkflowStore(init_db(settings.sqlite_path))
    names: dict[str, str] = {}
    for workflow_id in dict.fromkeys(workflow_ids):
        found = store.get_workflow(workflow_id)
        names[workflow_id] = str(found["name"]) if found else ""
    return names


@router.get("")
def list_executions(
    limit: int = Query(50, ge=1, le=500),
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> dict:
    """Newest executions first — the run list the client shows (item [77])."""
    from .db import init_db

    rows = ExecutionStore(init_db(settings.sqlite_path)).list_executions(limit=limit)
    names = _workflow_names(settings, [str(row["workflow_id"]) for row in rows])
    return {
        "total": len(rows),
        "executions": [
            {
                "id": row["id"],
                "workflow_id": row["workflow_id"],
                "workflow_name": names.get(str(row["workflow_id"]), ""),
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
    names = _workflow_names(settings, [str(execution["workflow_id"])])
    return {
        "execution_id": execution_id,
        "workflow_id": execution["workflow_id"],
        "workflow_name": names.get(str(execution["workflow_id"]), ""),
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


def _memory_summary(path: str) -> dict:
    """Memory item counts by state plus the reuse metrics (item [100])."""
    from memory.states import ALL_STATES
    from memory.store import MemoryStore
    from memory.usage import list_retrievals
    from memory.usage import summarize as summarize_reuse

    store = MemoryStore(path)
    by_state = {
        state: sum(1 for item in store.list_items(state=state, limit=10_000))
        for state in ALL_STATES
    }
    reuse = summarize_reuse(list_retrievals(path)).as_dict()
    return {
        "items_by_state": by_state,
        "reuse_hits": reuse.get("reuse_hits", 0),
        "hit_rate": reuse.get("hit_rate", 0.0),
    }


@metrics_router.get("/prometheus", response_class=PlainTextResponse)
def prometheus_metrics(
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> PlainTextResponse:
    """Prometheus text exposition of run, memory and build metrics (item [100]).

    Same permission as the JSON aggregates (`audit:read`): a scrape endpoint
    that leaks run counts to anonymous callers would be a new exposure, and a
    scraper can authenticate like any other client.
    """
    from .db import init_db

    path = init_db(settings.sqlite_path)
    stores = ExecutionStore(path)
    approvals = ApprovalService(path)
    summary = summarize(
        stores.list_executions(),
        steps=stores.list_all_steps(),
        requests=[asdict(req) for req in approvals.list_requests()],
        decisions=approvals.all_decisions(),
    ).as_dict()
    body = render_prometheus(
        summary,
        memory=_memory_summary(str(path)),
        build={"version": settings.app_version, "environment": settings.environment},
    )
    return PlainTextResponse(body, media_type=CONTENT_TYPE)
