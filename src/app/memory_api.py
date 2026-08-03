"""Phase 7 items [70], [71]: consent, memory and erasure endpoints (FR-14).

- `POST /memory/consent` / `DELETE /memory/consent` — the caller grants or
  revokes their own consent for the `memory:store` scope;
- `POST /memory/items` — store a validated plan (needs consent, is scrubbed
  and quarantined by the store);
- `GET /memory/items` and `GET /memory/search` — read side, filtered by
  risk/OS/state through `memory.retrieval`;
- `DELETE /memory/items/{id}` — erasure: the item and its events disappear,
  the consent history stays.

Authorization uses the same RBAC permissions as the rest of the API: reading
memory needs `audit:read`, storing/reusing needs `workflow:run`, and consent
is the authenticated caller's own record.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from memory.consent import SCOPE_MEMORY_STORE, ConsentError, ConsentStore
from memory.retrieval import RetrievalFilters, retrieve
from memory.states import ALL_STATES
from memory.store import RISK_LEVELS, MemoryError, MemoryStore

from .deps import CurrentUser, get_current_user, require_permission
from .roles import Permission
from .settings import Settings, get_settings

router = APIRouter(prefix="/memory", tags=["Memory"])


def _stores(settings: Settings) -> tuple[MemoryStore, ConsentStore]:
    from .db import init_db

    path = init_db(settings.sqlite_path)
    consents = ConsentStore(path)
    return MemoryStore(path, consents=consents), consents


class ConsentIn(BaseModel):
    granted: bool = True


class StoreItemIn(BaseModel):
    plan: dict | list = Field(description="canonical plan (mapping or list of steps)")
    summary: str = Field(default="", max_length=2000)
    intent: str = Field(default="", max_length=64)
    risk: str = "LOW"
    os_name: str = Field(default="", max_length=64)


@router.post("/consent")
def grant_consent(
    body: ConsentIn,
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Grant (or revoke) the caller's own memory:store consent."""
    _, consents = _stores(settings)
    if body.granted:
        consent = consents.grant(user.username, SCOPE_MEMORY_STORE)
    else:
        try:
            consent = consents.revoke(user.username, SCOPE_MEMORY_STORE)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {
        "subject": consent.subject,
        "scope": consent.scope,
        "active": consent.active,
        "granted_at": consent.granted_at,
        "revoked_at": consent.revoked_at,
    }


@router.get("/consent")
def read_consent(
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """The caller's own consent state (a missing record is simply inactive)."""
    _, consents = _stores(settings)
    try:
        consent = consents.get(user.username, SCOPE_MEMORY_STORE)
    except KeyError:
        return {"subject": user.username, "scope": SCOPE_MEMORY_STORE, "active": False}
    return {
        "subject": consent.subject,
        "scope": consent.scope,
        "active": consent.active,
        "granted_at": consent.granted_at,
        "revoked_at": consent.revoked_at,
    }


@router.post("/items", status_code=status.HTTP_201_CREATED)
def store_item(
    body: StoreItemIn,
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_permission(Permission.WORKFLOW_RUN)),
) -> dict:
    """Store a plan as shared memory (consent, scrub, quarantine)."""
    store, _ = _stores(settings)
    try:
        item = store.save(
            body.plan,
            subject=user.username,
            summary=body.summary,
            intent=body.intent,
            risk=body.risk,
            os_name=body.os_name,
        )
    except ConsentError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MemoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return item.as_dict()


@router.get("/items")
def list_items(
    state: str = Query("", description="filter by lifecycle state"),
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    """Every stored item (optionally one lifecycle state)."""
    store, _consents = _stores(settings)
    if state and state not in ALL_STATES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown state {state!r}, expected one of {list(ALL_STATES)}",
        )
    items = store.list_items(state=state)
    return {"total": len(items), "items": [item.as_dict() for item in items]}


@router.get("/search")
def search_items(
    q: str = Query(min_length=1, max_length=2000),
    top_k: int = Query(5, ge=1, le=50),
    risk: str = Query("MEDIUM"),
    os_name: str = Query(""),
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.AUDIT_READ)),
) -> dict:
    """Similarity search over reusable plans (quarantine excluded)."""
    if risk not in RISK_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown risk {risk!r}, expected one of {list(RISK_LEVELS)}",
        )
    store, _consents = _stores(settings)
    result = retrieve(
        q,
        store.list_items(),
        top_k=top_k,
        filters=RetrievalFilters(max_risk=risk, os_name=os_name),
    )
    return result.as_dict()


@router.delete("/items/{item_id}")
def erase_item(
    item_id: str,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.WORKFLOW_RUN)),
) -> dict:
    """Erase one stored plan (item [71]); the consent history is kept."""
    store, _consents = _stores(settings)
    if not store.erase(item_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown memory item {item_id!r}"
        )
    return {"erased": True, "item_id": item_id}
