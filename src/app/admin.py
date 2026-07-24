"""Phase 1 item [13]: OWNER-only user administration routes (FR-02 demo).

The first routes protected by :func:`app.deps.require_permission`:
``GET``/``POST /admin/users`` require ``member:manage`` (OWNER only), so a
token that merely *knows* the endpoint gets 403 unless its signed role
grants it. Storage is the sqlite auth database from item [11]/[12]; the
reproducible migration runner of item [15] owns schema application.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .db import create_user, init_db, set_role
from .deps import CurrentUser, require_permission
from .roles import Permission, Role, parse_role
from .settings import Settings, get_settings

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


def _auth_db(settings: Settings):
    """Apply the (idempotent) schema, then open the auth database."""
    return init_db(settings.sqlite_path)


@router.get("/users", response_model=list[UserOut])
def list_users(
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> list[UserOut]:
    """List users with their roles (OWNER only)."""
    db_path = _auth_db(settings)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, username, is_active FROM users ORDER BY username"
        ).fetchall()
        roles = dict(conn.execute("SELECT user_id, role FROM user_roles").fetchall())
    return [
        UserOut(
            id=row[0],
            username=row[1],
            role=roles.get(row[0], Role.VIEWER.value),
            is_active=bool(row[2]),
        )
        for row in rows
    ]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_user(
    body: CreateUserIn,
    settings: Settings = Depends(get_settings),
    actor: CurrentUser = Depends(require_permission(Permission.MEMBER_MANAGE)),
) -> UserOut:
    """Create a user with a role (OWNER only; duplicates are a 409)."""
    try:
        role = parse_role(body.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db_path = _auth_db(settings)
    try:
        user_id = create_user(db_path, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="username already exists"
        ) from exc
    set_role(db_path, user_id, role, granted_by=actor.username)
    return UserOut(id=user_id, username=body.username, role=role.value, is_active=True)
