"""Phase 0-2 database init: apply migrations/*.sql in order via stdlib sqlite3.

No SQLAlchemy/Alembic dependency yet (not installed, deferred with the
driver). `init_db` is idempotent (CREATE TABLE IF NOT EXISTS) and applies
every `NNN_*.sql` file sorted by name, so 001 (users) then 002 (audit).
Postgres execution of the same SQL files happens on the compose host.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def migration_files() -> list[Path]:
    """Sorted `NNN_*.sql` files under migrations/."""
    return sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql"))


def hash_password(password: str, salt: str = "phase0-salt") -> str:
    """PBKDF2-SHA256 verifier string `salt$iterations$hex` (stdlib only)."""
    iterations = 100_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), iterations)
    return f"{salt}${iterations}${digest.hex()}"


def init_db(path: str | Path) -> Path:
    """Create parent dirs and apply all migrations in order; returns db path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        for sql_file in migration_files():
            conn.executescript(sql_file.read_text(encoding="utf-8"))
    return target
