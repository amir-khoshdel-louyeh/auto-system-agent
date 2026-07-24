"""Phase 0-1 database helpers: migration application + user credentials.

No SQLAlchemy/Alembic dependency yet (not installed, deferred with the
driver). `init_db` is idempotent (CREATE TABLE IF NOT EXISTS) and applies
every `NNN_*.sql` file sorted by name, so 001 (users) then 002 (audit).
Postgres execution of the same SQL files happens on the compose host.

Phase 1 item [11]: password hashing delegates to `app.security` (random
per-password salt, PBKDF2-HMAC-SHA256) and the users table gains
`create_user` / `authenticate_user` helpers.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .security import hash_password, verify_password

__all__ = ["create_user", "authenticate_user", "hash_password", "init_db", "migration_files"]

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def migration_files() -> list[Path]:
    """Sorted `NNN_*.sql` files under migrations/."""
    return sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql"))


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


def init_db(path: str | Path) -> Path:
    """Create parent dirs and apply all migrations in order; returns db path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        for sql_file in migration_files():
            conn.executescript(sql_file.read_text(encoding="utf-8"))
    return target
