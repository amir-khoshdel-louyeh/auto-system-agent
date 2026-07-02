"""P2.5: audit repository CRUD plus logger dual-write."""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.storage.event_logger import EventLogger
from auto_system_agent.storage.repository import AuditRepository


class RepositoryCrudTests(unittest.TestCase):
    def _repo(self, tmp_path):
        return AuditRepository(Path(tmp_path) / "audit.db")

    def test_execution_lifecycle(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            execution_id = repo.record_execution(
                nl_intent="install vlc",
                canonical_cmd="sudo dnf install -y vlc",
                os_name="linux",
                verdict="CONFIRM",
                risk_score=35,
            )
            self.assertEqual(execution_id, 1)
            repo.finish_execution(execution_id, exit_code=0, compensation="sudo dnf remove -y vlc")
            row = repo.fetch_execution(execution_id)
            self.assertEqual(row["nl_intent"], "install vlc")
            self.assertEqual(row["canonical_cmd"], "sudo dnf install -y vlc")
            self.assertEqual(row["verdict"], "CONFIRM")
            self.assertEqual(row["risk_score"], 35)
            self.assertEqual(row["exit_code"], 0)
            self.assertEqual(row["compensation"], "sudo dnf remove -y vlc")
            self.assertTrue(row["ts_start"])
            self.assertTrue(row["ts_end"])

    def test_steps_stay_ordered_per_execution(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            execution_id = repo.record_execution(nl_intent="demo")
            repo.record_step(execution_id, seq=1, state="failed", stderr_ref="boom", retry_count=2)
            repo.record_step(execution_id, seq=0, state="done", stdout_ref="ok")
            steps = repo.fetch_execution(execution_id)["steps"]
            self.assertEqual([step["seq"] for step in steps], [0, 1])
            self.assertEqual(steps[0]["stdout_ref"], "ok")
            self.assertEqual(steps[1]["retry_count"], 2)

    def test_fetch_filters_and_counts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            repo.record_execution(nl_intent="a", verdict="ALLOW", risk_score=5)
            repo.record_execution(nl_intent="b", verdict="DENY", risk_score=85)
            self.assertEqual(repo.count_executions(), 2)
            newest_first = [row["nl_intent"] for row in repo.fetch_executions()]
            self.assertEqual(newest_first, ["b", "a"])
            denied = repo.fetch_executions(verdict="DENY")
            self.assertEqual([row["nl_intent"] for row in denied], ["b"])

    def test_unknown_execution_raises_key_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                self._repo(tmp).fetch_execution(999)

    def test_schema_indexes_exist_and_data_persists(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "audit.db"
            AuditRepository(db_path).record_execution(nl_intent="persisted")
            with sqlite3.connect(str(db_path)) as connection:
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
            self.assertTrue(
                {"idx_execution_ts_start", "idx_execution_exit_code", "idx_step_execution_id"}
                <= indexes
            )
            reopened = AuditRepository(db_path)
            self.assertEqual(reopened.count_executions(), 1)
            self.assertEqual(reopened.fetch_executions()[0]["nl_intent"], "persisted")


class LoggerDualWriteTests(unittest.TestCase):
    def test_jsonl_and_db_both_receive_event(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "events.jsonl"
            repo = AuditRepository(Path(tmp) / "audit.db")
            logger = EventLogger(log_path=log_path, repository=repo)
            logger.log(
                {
                    "mode": "multi_step",
                    "user_input": "install vlc",
                    "planned_tasks": [{"action": "run_command", "target": "sudo dnf install -y vlc"}],
                    "steps": [
                        {
                            "tool": "run_command",
                            "audit": {"decision": "approved", "risk_score": 35},
                            "result": {"success": True, "message": "installed"},
                        }
                    ],
                    "reply": "done",
                }
            )
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["user_input"], "install vlc")
            rows = repo.fetch_executions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["nl_intent"], "install vlc")
            self.assertEqual(rows[0]["canonical_cmd"], "sudo dnf install -y vlc")
            self.assertEqual(rows[0]["verdict"], "CONFIRM")
            steps = repo.fetch_execution(rows[0]["id"])["steps"]
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0]["state"], "done")

    def test_db_failure_never_breaks_jsonl(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "events.jsonl"

            class BrokenRepository:
                def record_execution(self, **kwargs):
                    raise RuntimeError("db down")

            EventLogger(log_path=log_path, repository=BrokenRepository()).log({"mode": "x"})
            self.assertTrue(log_path.exists())


if __name__ == "__main__":
    unittest.main()
