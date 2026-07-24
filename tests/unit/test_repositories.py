"""Phase 1 item [16]: repository interfaces + transaction boundaries.

Covers protocol conformance of the sqlite implementation, the
all-or-nothing guarantee of `transaction()` and the atomic
user+role unit of work used by POST /admin/users.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import create_user, init_db, list_users
from app.repositories import (
    SqliteRepository,
    TeamRepository,
    UserRepository,
    WorkspaceRepository,
    transaction,
)
from app.roles import Role
from storage.repository import AuditRepository, TemplateRepository


class ProtocolConformanceTests(unittest.TestCase):
    def test_sqlite_repository_implements_all_phase1_protocols(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SqliteRepository(init_db(Path(tmp) / "app.db"))
            self.assertIsInstance(repo, UserRepository)
            self.assertIsInstance(repo, TeamRepository)
            self.assertIsInstance(repo, WorkspaceRepository)

    def test_audit_repository_satisfies_template_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(AuditRepository(Path(tmp) / "audit.db"), TemplateRepository)

    def test_a_duck_type_missing_methods_is_not_a_user_repository(self):
        class Impostor:
            def create_user(self): ...

        self.assertNotIsInstance(Impostor(), UserRepository)


class TransactionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = init_db(Path(self._tmp.name) / "app.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _count(self, table: str) -> int:
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_transaction_commits_on_success(self):
        with sqlite3.connect(self.db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO teams (id, name, created_at) VALUES (?, ?, ?)",
                    ("t1", "committed-team", "2026-01-01T00:00:00"),
                )
        self.assertEqual(self._count("teams"), 1)

    def test_transaction_rolls_back_on_exception(self):
        with sqlite3.connect(self.db) as conn:
            with self.assertRaises(RuntimeError):
                with transaction(conn):
                    conn.execute(
                        "INSERT INTO teams (id, name, created_at) VALUES (?, ?, ?)",
                        ("t2", "doomed-team", "2026-01-01T00:00:00"),
                    )
                    raise RuntimeError("unit of work failed halfway")
        self.assertEqual(self._count("teams"), 0)

    def test_create_user_with_role_is_all_or_nothing(self):
        repo = SqliteRepository(self.db)
        user_id, role = repo.create_user_with_role("alice", "pw-12345", "OPERATOR")
        self.assertEqual(role, Role.OPERATOR)
        self.assertEqual(repo.get_role(user_id), Role.OPERATOR)
        users = list_users(self.db)
        self.assertEqual([u["username"] for u in users], ["alice"])

        # duplicate username fails inside the same boundary -> neither the
        # user nor a stray role row may survive
        with self.assertRaises(sqlite3.IntegrityError):
            repo.create_user_with_role("alice", "other-pw", "OWNER")
        self.assertEqual(self._count("users"), 1)
        self.assertEqual(self._count("user_roles"), 1)

    def test_invalid_role_rejected_before_any_write(self):
        repo = SqliteRepository(self.db)
        with self.assertRaises(ValueError):
            repo.create_user_with_role("bob", "pw-12345", "SUPERUSER")
        self.assertEqual(self._count("users"), 0)
        self.assertEqual(self._count("user_roles"), 0)

    def test_repository_delegates_match_module_functions(self):
        repo = SqliteRepository(self.db)
        via_repo = repo.create_user("carol", "pw-12345")
        via_module = create_user(self.db, "dave", "pw-12345")
        self.assertNotEqual(via_repo, via_module)
        usernames = {u["username"] for u in repo.list_users()}
        self.assertEqual(usernames, {"carol", "dave"})


if __name__ == "__main__":
    unittest.main()
