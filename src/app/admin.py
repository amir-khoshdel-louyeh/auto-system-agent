"""Phase 1 item [13]/[16]: OWNER-only user administration routes (FR-02).

The first routes protected by :func:`app.deps.require_permission`:
``GET``/``POST /admin/users`` require ``member:manage`` (OWNER only), so a
token that merely *knows* the endpoint gets 403 unless its signed role
grants it. Persistence goes through the :mod:`app.repositories`
interface; creating a user plus granting its role is one transaction
boundary (all-or-nothing).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .deps import CurrentUser, require_permission
from .repositories import SqliteRepository, get_repository
from .roles import Permission, parse_role

router = APIRouter(prefix="/admin", tags=["Administration"])


class CreateUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    role: str = "VIEWER"


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool


@router.get("/users", response_model=list[UserOut])
def list_users(
    repo: SqliteRepository = Depends(get_repository),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> list[UserOut]:
    """List users with their roles (OWNER only)."""
    return [UserOut(**row) for row in repo.list_users()]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_user(
    body: CreateUserIn,
    repo: SqliteRepository = Depends(get_repository),
    actor: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> UserOut:
    """Create a user with a role (OWNER only; duplicates are a 409)."""
    try:
        role = parse_role(body.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        user_id, granted = repo.create_user_with_role(
            body.username, body.password, role, granted_by=actor.username
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="username already exists"
        ) from exc
    return UserOut(id=user_id, username=body.username, role=granted.value, is_active=True)


# Re-exported for tests that type the dependency override.
__all__ = ["CreateUserIn", "UserOut", "router"]
