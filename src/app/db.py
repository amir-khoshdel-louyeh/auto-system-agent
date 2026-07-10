"""Phase 0 database init: apply migrations/001_init.sql via stdlib sqlite3.

No SQLAlchemy/Alembic dependency yet (not installed, deferred with the
driver). `init_db` is idempotent (CREATE TABLE IF NOT EXISTS) and is the
"migration تمیز" probe for Phase 0 Done; Postgres execution of the same
SQL file happens on the compose host.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
INIT_SQL = MIGRATIONS_DIR / "001_init.sql"


def hash_password(password: str, salt: str = "phase0-salt") -> str:
    """PBKDF2-SHA256 verifier string `salt$iterations$hex` (stdlib only)."""
    iterations = 100_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), iterations)
    return f"{salt}${iterations}${digest.hex()}"


def init_db(path: str | Path) -> Path:
    """Create parent dirs and apply 001_init.sql; returns the db path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    sql = INIT_SQL.read_text(encoding="utf-8")
    with sqlite3.connect(target) as conn:
        conn.executescript(sql)
    return target
