"""Phase 0 (local-only, not committed): migration 001 applies cleanly."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import hash_password, init_db


class AppMigrationTests(unittest.TestCase):
    def test_init_db_idempotent_and_users_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = init_db(Path(tmp) / "app.db")
            init_db(db)  # second apply must stay clean
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    ("u1", "admin", hash_password("s3cret"), "2026-01-01T00:00:00"),
                )
                row = conn.execute(
                    "SELECT username, is_active FROM users WHERE id = ?", ("u1",)
                ).fetchone()
        self.assertEqual(row, ("admin", 1))


if __name__ == "__main__":
    unittest.main()
