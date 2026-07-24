"""Phase 1 item [17]: migration integration tests (clean DB + reproducibility).

Gate evidence: a clean database migrates from scratch, every file is
recorded with its checksum, re-runs are no-ops, partial histories converge
incrementally, and edited/renamed applied migrations are rejected.
"""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import (
    MIGRATIONS_DIR,
    MigrationError,
    apply_migrations,
    authenticate_user,
    create_user,
    hash_password,
    init_db,
    migration_files,
    migration_status,
    run_migrations,
)

EXPECTED_TABLES = {
    # 001_init
    "users",
    # 002_audit
    "workflows",
    "workflow_versions",
    "executions",
    "approvals",
    "audit_events",
    # 003_user_roles
    "user_roles",
    # 004_workspaces
    "teams",
    "team_members",
    "workspaces",
    "workspace_members",
    # runner bookkeeping
    "schema_migrations",
}


class CleanDatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "app.db"

    def test_clean_database_migrates_every_file_once(self):
        applied = run_migrations(self.db)
        self.assertEqual(
            applied, [f.name for f in migration_files()], "clean run must apply all files"
        )
        with sqlite3.connect(self.db) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            recorded = conn.execute(
                "SELECT version, name, length(checksum), applied_at"
                " FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertTrue(EXPECTED_TABLES.issubset(tables), EXPECTED_TABLES - tables)
        self.assertEqual(len(recorded), len(migration_files()))
        expected = {f.stem.partition("_")[0]: f.stem.partition("_")[2] for f in migration_files()}
        for version, name, checksum_len, applied_at in recorded:
            self.assertEqual(expected.get(version), name)
            self.assertEqual(checksum_len, 64, "sha256 hex expected")
            self.assertTrue(applied_at)

    def test_second_run_is_a_noop(self):
        run_migrations(self.db)
        self.assertEqual(run_migrations(self.db), [])
        self.assertEqual(run_migrations(self.db), [])

    def test_init_db_remains_idempotent_and_users_usable(self):
        db = init_db(self.db)
        init_db(db)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                ("u1", "admin", hash_password("s3cret"), "2026-01-01T00:00:00"),
            )
            row = conn.execute(
                "SELECT username, is_active FROM users WHERE id = ?", ("u1",)
            ).fetchone()
        self.assertEqual(row, ("admin", 1))

    def test_incremental_convergence_from_partial_history(self):
        partial = Path(self._tmp.name) / "partial_migrations"
        partial.mkdir()
        shutil.copy(migration_files()[0], partial / migration_files()[0].name)
        with sqlite3.connect(self.db) as conn:
            first = apply_migrations(conn, partial)
            self.assertEqual(first, [migration_files()[0].name])
            # now point at the real directory: only the remainder applies
            rest = apply_migrations(conn, MIGRATIONS_DIR)
        self.assertEqual(rest, [f.name for f in migration_files()[1:]])

    def test_status_reports_applied_rows(self):
        run_migrations(self.db)
        rows = migration_status(self.db)
        self.assertEqual(len(rows), len(migration_files()))
        self.assertTrue(all(r["applied"] for r in rows))
        self.assertFalse(any(r["drift"] for r in rows))


class MigrationReproducibilityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "migrations"
        self.dir.mkdir()
        self.db = Path(self._tmp.name) / "app.db"
        (self.dir / "001_init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY);", encoding="utf-8"
        )

    def _apply(self) -> list[str]:
        with sqlite3.connect(self.db) as conn:
            return apply_migrations(conn, self.dir)

    def test_edited_applied_migration_is_rejected(self):
        self.assertEqual(self._apply(), ["001_init.sql"])
        (self.dir / "001_init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY);\n-- sneaky change",
            encoding="utf-8",
        )
        with self.assertRaises(MigrationError) as ctx:
            self._apply()
        self.assertIn("modified after being applied", str(ctx.exception))

    def test_renamed_applied_migration_is_rejected(self):
        self.assertEqual(self._apply(), ["001_init.sql"])
        (self.dir / "001_init.sql").rename(self.dir / "001_other.sql")
        with self.assertRaises(MigrationError):
            self._apply()

    def test_duplicate_version_prefix_is_rejected(self):
        (self.dir / "001_extra.sql").write_text("SELECT 1;", encoding="utf-8")
        with self.assertRaises(MigrationError) as ctx:
            self._apply()
        self.assertIn("duplicate migration version", str(ctx.exception))

    def test_badly_named_file_is_rejected(self):
        # matches the NNN_*.sql glob but has a non-numeric version prefix
        (self.dir / "12abc_demo.sql").write_text("SELECT 1;", encoding="utf-8")
        with self.assertRaises(MigrationError) as ctx:
            self._apply()
        self.assertIn("NNN_description.sql", str(ctx.exception))

    def test_missing_description_is_rejected(self):
        (self.dir / "009_.sql").write_text("SELECT 1;", encoding="utf-8")
        with self.assertRaises(MigrationError):
            self._apply()

    def test_status_flags_drift_without_applying(self):
        self.assertEqual(self._apply(), ["001_init.sql"])
        (self.dir / "001_init.sql").write_text("SELECT 1;", encoding="utf-8")
        rows = migration_status(self.db)
        self.assertTrue(rows[0]["applied"])
        self.assertTrue(rows[0]["drift"])


class MigrationAuthUsabilityTests(unittest.TestCase):
    def test_user_created_after_migration_authenticates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = init_db(Path(tmp) / "app.db")
            create_user(db, "alice", "pw-alice-1")
            self.assertIsNotNone(authenticate_user(db, "alice", "pw-alice-1"))
            self.assertIsNone(authenticate_user(db, "alice", "wrong"))
            self.assertIsNone(authenticate_user(db, "ghost", "pw-alice-1"))


if __name__ == "__main__":
    unittest.main()
