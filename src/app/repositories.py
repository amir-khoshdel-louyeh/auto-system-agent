"""Phase 1 item [16]: repository interfaces and transaction boundaries.

Typed :class:`typing.Protocol` contracts for the Phase 1 persistence
surface plus a concrete sqlite implementation the API layer depends on
(DI via :func:`get_repository`). Routes talk to the protocols, not to SQL
- swapping in the async Postgres driver later means providing another
implementation of the same contracts, not editing routes (proposal §8.1
"service-oriented monolith first").

Transaction boundary rule: a *unit of work* that must be all-or-nothing
(e.g. user + role) runs inside :func:`transaction`, which commits only
when the body returns and rolls back on any exception. Single-statement
repository methods are their own boundary.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from fastapi import Depends

from . import db
from .roles import Role
from .settings import Settings, get_settings


@runtime_checkable
class UserRepository(Protocol):
    """Identity + global role persistence (items [11], [12])."""

    def create_user(self, username: str, password: str) -> str: ...

    def create_user_with_role(
        self, username: str, password: str, role: Role | str, granted_by: str = ""
    ) -> tuple[str, Role]: ...

    def authenticate_user(self, username: str, password: str) -> str | None: ...

    def get_role(self, user_id: str) -> Role: ...

    def set_role(self, user_id: str, role: Role | str, granted_by: str = "") -> Role: ...

    def user_id_by_username(self, username: str) -> str | None: ...

    def list_users(self) -> list[dict]: ...


@runtime_checkable
class TeamRepository(Protocol):
    """Team persistence (item [14])."""

    def create_team(self, name: str, created_by: str = "") -> str: ...

    def list_teams(self) -> list[dict]: ...

    def add_team_member(self, team_id: str, user_id: str, role: Role | str) -> Role: ...

    def list_team_members(self, team_id: str) -> list[dict]: ...


@runtime_checkable
class WorkspaceRepository(Protocol):
    """Workspace persistence with scoped roles (item [14])."""

    def create_workspace(self, name: str, created_by: str = "", team_id: str = "") -> str: ...

    def list_workspaces(self) -> list[dict]: ...

    def add_workspace_member(self, workspace_id: str, user_id: str, role: Role | str) -> Role: ...

    def list_workspace_members(self, workspace_id: str) -> list[dict]: ...

    def effective_role(self, user_id: str, workspace_id: str) -> Role: ...


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception, always close cleanly.

    This is the project's transaction boundary for multi-statement units of
    work; callers never manage commit/rollback themselves.
    """
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


class SqliteRepository:
    """Path-bound sqlite implementation of all Phase 1 protocols.

    Every method opens its own connection: fine for the stdlib phase, and
    the async Postgres implementation will honour the same protocol shape
    under FastAPI's dependency injection.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- users ------------------------------------------------------------
    def create_user(self, username: str, password: str) -> str:
        return db.create_user(self.path, username, password)

    def create_user_with_role(
        self, username: str, password: str, role: Role | str, granted_by: str = ""
    ) -> tuple[str, Role]:
        """One atomic unit of work: user row + role row or neither."""
        parsed = db.parse_role(role)
        conn = sqlite3.connect(self.path)
        try:
            with transaction(conn):
                user_id = db.create_user_row(conn, username, password)
                db.grant_role_row(conn, user_id, parsed, granted_by)
        finally:
            conn.close()
        return user_id, parsed

    def authenticate_user(self, username: str, password: str) -> str | None:
        return db.authenticate_user(self.path, username, password)

    def get_role(self, user_id: str) -> Role:
        return db.get_role(self.path, user_id)

    def set_role(self, user_id: str, role: Role | str, granted_by: str = "") -> Role:
        return db.set_role(self.path, user_id, role, granted_by)

    def user_id_by_username(self, username: str) -> str | None:
        return db.user_id_by_username(self.path, username)

    def list_users(self) -> list[dict]:
        return db.list_users(self.path)

    # -- teams ------------------------------------------------------------
    def create_team(self, name: str, created_by: str = "") -> str:
        return db.create_team(self.path, name, created_by)

    def list_teams(self) -> list[dict]:
        return db.list_teams(self.path)

    def add_team_member(self, team_id: str, user_id: str, role: Role | str) -> Role:
        return db.add_team_member(self.path, team_id, user_id, role)

    def list_team_members(self, team_id: str) -> list[dict]:
        return db.list_team_members(self.path, team_id)

    # -- workspaces -------------------------------------------------------
    def create_workspace(self, name: str, created_by: str = "", team_id: str = "") -> str:
        return db.create_workspace(self.path, name, created_by, team_id)

    def list_workspaces(self) -> list[dict]:
        return db.list_workspaces(self.path)

    def add_workspace_member(self, workspace_id: str, user_id: str, role: Role | str) -> Role:
        return db.add_workspace_member(self.path, workspace_id, user_id, role)

    def list_workspace_members(self, workspace_id: str) -> list[dict]:
        return db.list_workspace_members(self.path, workspace_id)

    def effective_role(self, user_id: str, workspace_id: str) -> Role:
        return db.effective_role(self.path, user_id, workspace_id)


def get_repository(settings: Settings = Depends(get_settings)) -> SqliteRepository:
    """FastAPI dependency: one repository bound to the configured auth DB.

    The database path is initialised here (idempotent migration runner of
    item [15]) so every route works on a schema that is already migrated.
    """
    path = db.init_db(settings.sqlite_path)
    return SqliteRepository(path)
