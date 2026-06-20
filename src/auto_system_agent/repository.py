"""SQLite audit store for executions and per-step records (P2.4).

Tables mirror proposal.txt P2.4: Execution(id, ts_start, ts_end,
nl_intent, canonical_cmd, os, verdict, risk_score, exit_code,
compensation) + Step(id, execution_id FK, seq, state, stdout_ref,
stderr_ref, retry_count), with indexes on (ts_start, exit_code).

Connections are opened per operation so threaded GUI workers never share
a handle. Logging callers must still guard writes (see event_logger).
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".auto_system_agent" / "audit.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_start TEXT NOT NULL,
    ts_end TEXT,
    nl_intent TEXT NOT NULL DEFAULT '',
    canonical_cmd TEXT NOT NULL DEFAULT '',
    os TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL DEFAULT '',
    risk_score INTEGER,
    exit_code INTEGER,
    compensation TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS step (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL REFERENCES execution(id),
    seq INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT '',
    stdout_ref TEXT NOT NULL DEFAULT '',
    stderr_ref TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_execution_ts_start ON execution(ts_start);
CREATE INDEX IF NOT EXISTS idx_execution_exit_code ON execution(exit_code);
CREATE INDEX IF NOT EXISTS idx_step_execution_id ON step(execution_id);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditRepository:
    """Small audit store; one row per execution plus ordered step rows."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._init_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def record_execution(
        self,
        *,
        nl_intent: str = "",
        canonical_cmd: str = "",
        os_name: str = "",
        verdict: str = "",
        risk_score: int | None = None,
        exit_code: int | None = None,
        compensation: str = "",
        ts_start: str | None = None,
    ) -> int:
        """Insert one execution row; returns its id."""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO execution "
                "(ts_start, nl_intent, canonical_cmd, os, verdict,"
                " risk_score, exit_code, compensation)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts_start or utc_now_iso(),
                    nl_intent,
                    canonical_cmd,
                    os_name,
                    verdict,
                    risk_score,
                    exit_code,
                    compensation,
                ),
            )
            return int(cursor.lastrowid)

    def finish_execution(
        self,
        execution_id: int,
        *,
        exit_code: int | None = None,
        ts_end: str | None = None,
        compensation: str | None = None,
    ) -> None:
        """Stamp completion fields on one execution row."""
        fields: list[str] = ["ts_end = ?"]
        values: list[object] = [ts_end or utc_now_iso()]
        if exit_code is not None:
            fields.append("exit_code = ?")
            values.append(exit_code)
        if compensation is not None:
            fields.append("compensation = ?")
            values.append(compensation)
        values.append(execution_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE execution SET {', '.join(fields)} WHERE id = ?",
                values,
            )
