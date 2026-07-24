"""Phase 1 item [14]/[16]: teams, members and workspaces API (§5, §9, FR-02).

Containers own their own role assignments: ``workspace:manage`` (OWNER)
creates containers and edits memberships, ``member:manage`` manages team
memberships, ``run:read`` may list them. ``GET /workspaces/{id}/role``
reports the *effective* role of the caller - workspace membership
overrides the global role, falling back to least privilege (VIEWER).

Persistence goes through the :mod:`app.repositories` interface so the
routes stay identical when the Postgres implementation arrives.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .deps import CurrentUser, get_current_user, require_permission
from .repositories import SqliteRepository, get_repository
from .roles import Permission

teams_router = APIRouter(prefix="/teams", tags=["Teams"])
workspaces_router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class WorkspaceIn(NameIn):
    team_id: str = ""


class MemberIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    role: str = "VIEWER"


def _resolve_user(repo: SqliteRepository, username: str) -> str:
    user_id = repo.user_id_by_username(username)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown user: {username}"
        )
    return user_id


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------
@teams_router.post("", status_code=status.HTTP_201_CREATED)
def post_team(
    body: NameIn,
    repo: SqliteRepository = Depends(get_repository),
    actor: CurrentUser = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
) -> dict:
    try:
        team_id = repo.create_team(body.name, created_by=actor.username)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"id": team_id, "name": body.name.strip()}


@teams_router.get("")
def get_teams(
    repo: SqliteRepository = Depends(get_repository),
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> list[dict]:
    return repo.list_teams()


@teams_router.post("/{team_id}/members", status_code=status.HTTP_201_CREATED)
def post_team_member(
    team_id: str,
    body: MemberIn,
    repo: SqliteRepository = Depends(get_repository),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> dict:
    user_id = _resolve_user(repo, body.username)
    try:
        role = repo.add_team_member(team_id, user_id, body.role)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"team_id": team_id, "user_id": user_id, "role": role.value}


@teams_router.get("/{team_id}/members")
def get_team_members(
    team_id: str,
    repo: SqliteRepository = Depends(get_repository),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> list[dict]:
    return repo.list_team_members(team_id)


# --------------------------------------------------------------------------
# Workspaces
# --------------------------------------------------------------------------
@workspaces_router.post("", status_code=status.HTTP_201_CREATED)
def post_workspace(
    body: WorkspaceIn,
    repo: SqliteRepository = Depends(get_repository),
    actor: CurrentUser = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
) -> dict:
    try:
        workspace_id = repo.create_workspace(
            body.name, created_by=actor.username, team_id=body.team_id
        )
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"id": workspace_id, "name": body.name.strip(), "team_id": body.team_id}


@workspaces_router.get("")
def get_workspaces(
    repo: SqliteRepository = Depends(get_repository),
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> list[dict]:
    return repo.list_workspaces()


@workspaces_router.post("/{workspace_id}/members", status_code=status.HTTP_201_CREATED)
def post_workspace_member(
    workspace_id: str,
    body: MemberIn,
    repo: SqliteRepository = Depends(get_repository),
    _: CurrentUser = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
) -> dict:
    user_id = _resolve_user(repo, body.username)
    try:
        role = repo.add_workspace_member(workspace_id, user_id, body.role)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"workspace_id": workspace_id, "user_id": user_id, "role": role.value}


@workspaces_router.get("/{workspace_id}/members")
def get_workspace_members(
    workspace_id: str,
    repo: SqliteRepository = Depends(get_repository),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> list[dict]:
    return repo.list_workspace_members(workspace_id)


@workspaces_router.get("/{workspace_id}/role")
def get_my_workspace_role(
    workspace_id: str,
    repo: SqliteRepository = Depends(get_repository),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Effective role of the caller inside this workspace (fail closed)."""
    # The env-admin may not exist in the users table; when the caller has no
    # user row there is no workspace membership either, so the token's
    # global role is already the effective (least-privileged) answer.
    resolved = repo.user_id_by_username(user.username)
    if resolved is None:
        return {
            "workspace_id": workspace_id,
            "username": user.username,
            "role": user.role.value,
        }
    return {
        "workspace_id": workspace_id,
        "username": user.username,
        "role": repo.effective_role(resolved, workspace_id).value,
    }
