"""Phase 1 item [14]: teams, members and workspaces API (§5, §9, FR-02).

Containers own their own role assignments: ``workspace:manage`` (OWNER)
creates containers and edits memberships, ``member:manage`` manages team
memberships, ``run:read`` may list them. ``GET /workspaces/{id}/role``
reports the *effective* role of the caller - workspace membership
overrides the global role, falling back to least privilege (VIEWER).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .db import (
    add_team_member,
    add_workspace_member,
    create_team,
    create_workspace,
    effective_role,
    list_team_members,
    list_teams,
    list_workspace_members,
    list_workspaces,
    user_id_by_username,
)
from .deps import CurrentUser, get_current_user, require_permission
from .roles import Permission
from .settings import Settings, get_settings

teams_router = APIRouter(prefix="/teams", tags=["Teams"])
workspaces_router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class MemberIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    role: str = "VIEWER"


def _db(settings: Settings):
    from .db import init_db

    return init_db(settings.sqlite_path)


def _resolve_user(db_path, username: str) -> str:
    user_id = user_id_by_username(db_path, username)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown user: {username}"
        )
    return user_id


def _conflict(exc: sqlite3.IntegrityError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------
@teams_router.post("", status_code=status.HTTP_201_CREATED)
def post_team(
    body: NameIn,
    settings: Settings = Depends(get_settings),
    actor: CurrentUser = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
) -> dict:
    try:
        team_id = create_team(_db(settings), body.name, created_by=actor.username)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except sqlite3.IntegrityError as exc:
        raise _conflict(exc) from exc
    return {"id": team_id, "name": body.name.strip()}


@teams_router.get("")
def get_teams(
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> list[dict]:
    return list_teams(_db(settings))


@teams_router.post("/{team_id}/members", status_code=status.HTTP_201_CREATED)
def post_team_member(
    team_id: str,
    body: MemberIn,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> dict:
    db_path = _db(settings)
    user_id = _resolve_user(db_path, body.username)
    try:
        role = add_team_member(db_path, team_id, user_id, body.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"team_id": team_id, "user_id": user_id, "role": role.value}


@teams_router.get("/{team_id}/members")
def get_team_members(
    team_id: str,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> list[dict]:
    return list_team_members(_db(settings), team_id)


# --------------------------------------------------------------------------
# Workspaces
# --------------------------------------------------------------------------
class WorkspaceIn(NameIn):
    team_id: str = ""


@workspaces_router.post("", status_code=status.HTTP_201_CREATED)
def post_workspace(
    body: WorkspaceIn,
    settings: Settings = Depends(get_settings),
    actor: CurrentUser = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
) -> dict:
    try:
        workspace_id = create_workspace(
            _db(settings), body.name, created_by=actor.username, team_id=body.team_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except sqlite3.IntegrityError as exc:
        raise _conflict(exc) from exc
    return {"id": workspace_id, "name": body.name.strip(), "team_id": body.team_id}


@workspaces_router.get("")
def get_workspaces(
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> list[dict]:
    return list_workspaces(_db(settings))


@workspaces_router.post("/{workspace_id}/members", status_code=status.HTTP_201_CREATED)
def post_workspace_member(
    workspace_id: str,
    body: MemberIn,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
) -> dict:
    db_path = _db(settings)
    user_id = _resolve_user(db_path, body.username)
    try:
        role = add_workspace_member(db_path, workspace_id, user_id, body.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"workspace_id": workspace_id, "user_id": user_id, "role": role.value}


@workspaces_router.get("/{workspace_id}/members")
def get_workspace_members(
    workspace_id: str,
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> list[dict]:
    return list_workspace_members(_db(settings), workspace_id)


@workspaces_router.get("/{workspace_id}/role")
def get_my_workspace_role(
    workspace_id: str,
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Effective role of the caller inside this workspace (fail closed)."""
    db_path = _db(settings)
    # The env-admin may not exist in the users table; when the caller has no
    # user row there is no workspace membership either, so the token's
    # global role is already the effective (least-privileged) answer.
    resolved = user_id_by_username(db_path, user.username)
    if resolved is None:
        return {"workspace_id": workspace_id, "username": user.username, "role": user.role.value}
    return {
        "workspace_id": workspace_id,
        "username": user.username,
        "role": effective_role(db_path, resolved, workspace_id).value,
    }
