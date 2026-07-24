"""Phase 0-1 database helpers: reproducible migrations + credentials.

Item [15]: `run_migrations` is the reproducible migration runner —
`NNN_*.sql` files are applied in order, each recorded in the
`schema_migrations` table with its SHA-256 checksum; re-running applies
nothing twice, and any edit or rename of an already-applied migration
raises :class:`MigrationError` (drift detection) instead of silently
diverging between environments. `init_db` is the sqlite convenience
wrapper around it; the Postgres port reuses the same files and tracking
logic through :func:`apply_migrations_to_connection`.

Item [11]: password hashing delegates to `app.security` (random
per-password salt, PBKDF2-HMAC-SHA256) and the users table gains
`create_user` / `authenticate_user` helpers.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .roles import Role, parse_role
from .security import hash_password, verify_password


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "MigrationError",
    "add_team_member",
    "add_workspace_member",
    "apply_migrations",
    "authenticate_user",
    "create_team",
    "create_user",
    "create_workspace",
    "effective_role",
    "get_role",
    "hash_password",
    "init_db",
    "list_team_members",
    "list_teams",
    "list_workspace_members",
    "list_workspaces",
    "migration_files",
    "migration_status",
    "run_migrations",
    "set_role",
    "user_id_by_username",
]

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class MigrationError(RuntimeError):
    """Migration history diverged (edited/renamed applied migration)."""


def migration_files() -> list[Path]:
    """Sorted `NNN_*.sql` files under migrations/."""
    return sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql"))


def _file_checksum(sql_file: Path) -> str:
    return hashlib.sha256(sql_file.read_bytes()).hexdigest()


def _migration_identity(sql_file: Path) -> tuple[str, str]:
    """(version, name) for a migration file — version is the `NNN` prefix."""
    version, _, name = sql_file.stem.partition("_")
    if not version.isdigit() or not name:
        raise MigrationError(f"migration filename must be NNN_description.sql: {sql_file.name}")
    return version, name


def create_user(path: str | Path, username: str, password: str) -> str:
    """Insert an active user with a salted PBKDF2 verifier; returns the id.

    Raises ``sqlite3.IntegrityError`` for a duplicate username (the UNIQUE
    constraint stays the single source of truth).
    """
    if not username.strip() or not password:
        raise ValueError("username and password must be non-empty")
    user_id = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_active, created_at)"
            " VALUES (?, ?, ?, 1, ?)",
            (user_id, username, hash_password(password), created),
        )
    return user_id


def authenticate_user(path: str | Path, username: str, password: str) -> str | None:
    """Return the user id when credentials verify and the account is active.

    Unknown user, disabled account and bad password are indistinguishable
    to the caller (all return ``None``); verification is constant-time.
    """
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT id, password_hash, is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if row is None or not row[2]:
        return None
    return row[0] if verify_password(password, row[1]) else None


def set_role(path: str | Path, user_id: str, role: Role | str, granted_by: str = "") -> Role:
    """Grant ``role`` to a user (item [12]); returns the parsed role."""
    parsed = parse_role(role)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO user_roles (user_id, role, granted_at, granted_by)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET role = excluded.role,"
            " granted_at = excluded.granted_at, granted_by = excluded.granted_by",
            (user_id, parsed.value, now, granted_by),
        )
    return parsed


def get_role(path: str | Path, user_id: str) -> Role:
    """Current role of a user; no row means least privilege (VIEWER)."""
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return Role.VIEWER
    return parse_role(row[0])


def user_id_by_username(path: str | Path, username: str) -> str | None:
    """Resolve a username to its user id (``None`` when unknown)."""
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------
# Teams and workspaces (item [14])
# --------------------------------------------------------------------------
def create_team(path: str | Path, name: str, created_by: str = "") -> str:
    """Insert a team; ValueError on empty name, IntegrityError on duplicate."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("team name is empty")
    team_id = uuid.uuid4().hex
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO teams (id, name, created_at, created_by) VALUES (?, ?, ?, ?)",
            (team_id, clean, _utc_now(), created_by),
        )
    return team_id


def list_teams(path: str | Path) -> list[dict]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, created_by FROM teams ORDER BY name"
        ).fetchall()
    return [{"id": r[0], "name": r[1], "created_at": r[2], "created_by": r[3]} for r in rows]


def add_team_member(path: str | Path, team_id: str, user_id: str, role: Role | str) -> Role:
    """Add/replace a team membership (idempotent upsert)."""
    parsed = parse_role(role)
    with sqlite3.connect(path) as conn:
        exists = conn.execute("SELECT 1 FROM teams WHERE id = ?", (team_id,)).fetchone()
        if not exists:
            raise LookupError(f"unknown team: {team_id}")
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, role, joined_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(team_id, user_id) DO UPDATE SET role = excluded.role,"
            " joined_at = excluded.joined_at",
            (team_id, user_id, parsed.value, _utc_now()),
        )
    return parsed


def list_team_members(path: str | Path, team_id: str) -> list[dict]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT tm.user_id, tm.role, tm.joined_at, u.username"
            " FROM team_members tm JOIN users u ON u.id = tm.user_id"
            " WHERE tm.team_id = ? ORDER BY u.username",
            (team_id,),
        ).fetchall()
    return [{"user_id": r[0], "role": r[1], "joined_at": r[2], "username": r[3]} for r in rows]


def create_workspace(path: str | Path, name: str, created_by: str = "", team_id: str = "") -> str:
    """Insert a workspace; ValueError on empty name, IntegrityError on duplicate."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("workspace name is empty")
    workspace_id = uuid.uuid4().hex
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO workspaces (id, name, team_id, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?)",
            (workspace_id, clean, team_id, _utc_now(), created_by),
        )
    return workspace_id


def list_workspaces(path: str | Path) -> list[dict]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT id, name, team_id, created_at, created_by FROM workspaces ORDER BY name"
        ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "team_id": r[2],
            "created_at": r[3],
            "created_by": r[4],
        }
        for r in rows
    ]


def add_workspace_member(
    path: str | Path, workspace_id: str, user_id: str, role: Role | str
) -> Role:
    """Add/replace a workspace membership (idempotent upsert)."""
    parsed = parse_role(role)
    with sqlite3.connect(path) as conn:
        exists = conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not exists:
            raise LookupError(f"unknown workspace: {workspace_id}")
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, joined_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = excluded.role,"
            " joined_at = excluded.joined_at",
            (workspace_id, user_id, parsed.value, _utc_now()),
        )
    return parsed


def list_workspace_members(path: str | Path, workspace_id: str) -> list[dict]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT wm.user_id, wm.role, wm.joined_at, u.username"
            " FROM workspace_members wm JOIN users u ON u.id = wm.user_id"
            " WHERE wm.workspace_id = ? ORDER BY u.username",
            (workspace_id,),
        ).fetchall()
    return [{"user_id": r[0], "role": r[1], "joined_at": r[2], "username": r[3]} for r in rows]


def effective_role(path: str | Path, user_id: str, workspace_id: str) -> Role:
    """Workspace-scoped role: membership overrides the global role.

    Precedence (fail closed to VIEWER): workspace membership, then the
    global ``user_roles`` row, then least privilege.
    """
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchone()
    if row is not None:
        return parse_role(row[0])
    return get_role(path, user_id)


def init_db(path: str | Path) -> Path:
    """Create parent dirs and apply pending migrations; returns the db path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    run_migrations(target)
    return target


def _ensure_tracking_table(conn: Any) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " checksum TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )


def apply_migrations(conn: Any, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations on a DB-API connection (sqlite or postgres).

    Returns the applied file names. Records version, name and SHA-256
    checksum; raises :class:`MigrationError` when an applied migration was
    edited or renamed, so every environment converges on one history.
    """
    _ensure_tracking_table(conn)
    rows = conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
    recorded: dict[str, tuple[str, str]] = {row[0]: (row[1], row[2]) for row in rows}
    applied: list[str] = []
    files = sorted(migrations_dir.glob("[0-9]*_*.sql"))
    seen_versions: set[str] = set()
    for sql_file in files:
        version, name = _migration_identity(sql_file)
        if version in seen_versions:
            raise MigrationError(f"duplicate migration version {version}: {sql_file.name}")
        seen_versions.add(version)
        checksum = _file_checksum(sql_file)
        if version in recorded:
            prev_name, prev_checksum = recorded[version]
            if prev_name != name or prev_checksum != checksum:
                raise MigrationError(
                    f"migration {version} was modified after being applied "
                    f"(recorded {prev_name}:{prev_checksum[:12]}, "
                    f"now {name}:{checksum[:12]}) — create a new migration instead"
                )
            continue
        conn.executescript(sql_file.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
            " VALUES (?, ?, ?, ?)",
            (version, name, checksum, _utc_now()),
        )
        applied.append(sql_file.name)
    return applied


def run_migrations(path: str | Path) -> list[str]:
    """sqlite runner: apply pending migrations; returns applied file names."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        return apply_migrations(conn)


def migration_status(path: str | Path) -> list[dict]:
    """One row per migration file: applied flag, timestamp and checksum."""
    target = Path(path)
    recorded: dict[str, tuple[str, str, str]] = {}
    if target.exists():
        with sqlite3.connect(target) as conn:
            try:
                rows = conn.execute(
                    "SELECT version, name, checksum, applied_at FROM schema_migrations"
                ).fetchall()
            except sqlite3.OperationalError:  # tracking table not created yet
                rows = []
        recorded = {row[0]: (row[1], row[2], row[3]) for row in rows}
    status_rows: list[dict] = []
    for sql_file in migration_files():
        version, name = _migration_identity(sql_file)
        entry = recorded.get(version)
        checksum = _file_checksum(sql_file)
        status_rows.append(
            {
                "version": version,
                "name": name,
                "file": sql_file.name,
                "checksum": checksum,
                "applied": entry is not None,
                "applied_at": entry[2] if entry else "",
                "drift": bool(entry and (entry[0] != name or entry[1] != checksum)),
            }
        )
    return status_rows


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    import argparse

    parser = argparse.ArgumentParser(description="Apply pending database migrations")
    parser.add_argument("--db", default="data/app.db", help="sqlite database path")
    parser.add_argument("--status", action="store_true", help="print status, apply nothing")
    args = parser.parse_args()
    if args.status:
        for row in migration_status(args.db):
            mark = "APPLIED" if row["applied"] else "PENDING"
            if row["drift"]:
                mark = "DRIFT!"
            print(f"{row['version']}  {row['file']:<32} {mark} {row['applied_at']}")
    else:
        done = run_migrations(args.db)
        print(f"applied {len(done)} migration(s): {', '.join(done) or '(none, up to date)'}")
