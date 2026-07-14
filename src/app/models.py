"""Phase 2 audit row models: workflows/versions/executions/approvals (A3 + A7).

Plain dataclasses over migration 002 tables (stdlib sqlite3 via `Recorder`);
SQLAlchemy/Alembic wiring arrives with the driver phase. IDs are uuid-hex
and timestamps ISO-8601 UTC, matching the portable-SQL convention.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    """Random TEXT primary key."""
    return uuid.uuid4().hex


@dataclass
class ExecutionRow:
    """One row of the `executions` table."""

    id: str
    workflow_id: str
    version: int
    status: str
    triggered_by: str = "manual"
    started_at: str = ""
    finished_at: str = ""
    error: str = ""


@dataclass
class ApprovalRow:
    """One row of the `approvals` table."""

    id: str
    execution_id: str
    node_id: str
    verdict: str
    decided_by: str = ""
    decided_at: str = ""
    reason: str = ""


class Recorder:
    """Minimal row writer for runs and approvals (read models stay dicts)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_workflow(self, name: str) -> str:
        """Return the id for `name`, creating the workflow row if needed."""
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM workflows WHERE name = ?", (name,)).fetchone()
            if row:
                return row["id"]
            wid = new_id()
            conn.execute(
                "INSERT INTO workflows (id, name, created_at) VALUES (?, ?, ?)",
                (wid, name, utcnow()),
            )
            return wid

    def add_version(self, workflow_id: str, version: int, dsl_text: str, dsl_hash: str) -> str:
        """Insert a workflow version row (idempotent per workflow+version)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM workflow_versions WHERE workflow_id = ? AND version = ?",
                (workflow_id, version),
            ).fetchone()
            if row:
                return row["id"]
            vid = new_id()
            conn.execute(
                "INSERT INTO workflow_versions (id, workflow_id, version, dsl_text,"
                " dsl_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (vid, workflow_id, version, dsl_text, dsl_hash, utcnow()),
            )
            return vid

    def start_execution(self, workflow_id: str, version: int, triggered_by: str = "manual") -> ExecutionRow:
        """Insert a RUNNING execution row."""
        row = ExecutionRow(
            id=new_id(),
            workflow_id=workflow_id,
            version=version,
            status="RUNNING",
            triggered_by=triggered_by,
            started_at=utcnow(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO executions (id, workflow_id, version, status, triggered_by,"
                " started_at, finished_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row.id, row.workflow_id, row.version, row.status, row.triggered_by,
                 row.started_at, row.finished_at, row.error),
            )
        return row

    def finish_execution(self, execution_id: str, status: str, error: str = "") -> None:
        """Mark an execution terminal."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE executions SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, utcnow(), error, execution_id),
            )

    def add_approval(self, execution_id: str, node_id: str, verdict: str,
                     decided_by: str = "", reason: str = "") -> ApprovalRow:
        """Insert an approval decision row."""
        row = ApprovalRow(
            id=new_id(),
            execution_id=execution_id,
            node_id=node_id,
            verdict=verdict,
            decided_by=decided_by,
            decided_at=utcnow(),
            reason=reason,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO approvals (id, execution_id, node_id, verdict, decided_by,"
                " decided_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row.id, row.execution_id, row.node_id, row.verdict, row.decided_by,
                 row.decided_at, row.reason),
            )
        return row
