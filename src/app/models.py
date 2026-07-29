"""Workflow/version and execution persistence (A3 + A7; items [27], [28]).

Plain stores over the migration tables (stdlib sqlite3):

- :class:`WorkflowStore` — workflows, immutable versions and triggers;
- :class:`ExecutionStore` — executions and per-node execution steps;
- :class:`Recorder` — the small row writer the bus dispatcher uses.

IDs are uuid-hex and timestamps ISO-8601 UTC, matching the portable-SQL
convention; SQLAlchemy/Alembic wiring arrives with the driver phase.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from engine.executor.states import state_for


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

    def start_execution(
        self, workflow_id: str, version: int, triggered_by: str = "manual"
    ) -> ExecutionRow:
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
                (
                    row.id,
                    row.workflow_id,
                    row.version,
                    row.status,
                    row.triggered_by,
                    row.started_at,
                    row.finished_at,
                    row.error,
                ),
            )
        return row

    def finish_execution(self, execution_id: str, status: str, error: str = "") -> None:
        """Mark an execution terminal."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE executions SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, utcnow(), error, execution_id),
            )

    def add_approval(
        self, execution_id: str, node_id: str, verdict: str, decided_by: str = "", reason: str = ""
    ) -> ApprovalRow:
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
                (
                    row.id,
                    row.execution_id,
                    row.node_id,
                    row.verdict,
                    row.decided_by,
                    row.decided_at,
                    row.reason,
                ),
            )
        return row


def dsl_hash(text: str) -> str:
    """Stable content hash of one workflow version's DSL text."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def summarize_output(output: dict | None, limit: int = 500) -> str:
    """A bounded summary of a node output — raw output is never persisted.

    FR-11: the result is serialised deterministically and truncated so a
    huge or noisy payload cannot bloat the run table or leak more detail
    than the operator timeline needs.
    """
    try:
        text = json.dumps(output or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        text = str(output)
    return text[:limit]


class ImmutableVersionError(RuntimeError):
    """A published ``(workflow_id, version)`` cannot be overwritten (§9 inv. 2)."""


class WorkflowStore:
    """Workflow + immutable version persistence (item [27], FR-03/FR-05).

    A workflow is keyed by its unique name; each publication adds one
    ``workflow_versions`` row keyed by ``(workflow_id, version)``. Once a
    version exists it is immutable: re-publishing the *same* DSL text is an
    idempotent no-op, while republishing different text under the same
    version raises :class:`ImmutableVersionError`.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_workflow(self, name: str, *, workspace_id: str = "", created_by: str = "") -> str:
        """Return the id for `name`, creating the workflow row if needed."""
        clean = (name or "").strip()
        if not clean:
            raise ValueError("workflow name is empty")
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM workflows WHERE name = ?", (clean,)).fetchone()
            if row:
                return str(row["id"])
            wid = new_id()
            conn.execute(
                "INSERT INTO workflows (id, name, workspace_id, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (wid, clean, workspace_id, created_by, utcnow()),
            )
            return wid

    def get_workflow(self, workflow_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        return dict(row) if row else None

    def get_by_name(self, name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def list_workflows(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM workflows ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def publish_version(
        self, workflow_id: str, version: int, dsl_text: str, *, published_by: str = ""
    ) -> dict:
        """Insert one immutable version row (idempotent for identical text)."""
        if not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        digest = dsl_hash(dsl_text)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_versions WHERE workflow_id = ? AND version = ?",
                (workflow_id, version),
            ).fetchone()
            if row:
                if row["dsl_hash"] != digest:
                    raise ImmutableVersionError(
                        f"workflow {workflow_id} version {version} is published and"
                        " immutable; publish a new version instead"
                    )
                return dict(row)
            vid = new_id()
            now = utcnow()
            conn.execute(
                "INSERT INTO workflow_versions (id, workflow_id, version, dsl_text, dsl_hash,"
                " published_by, published_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (vid, workflow_id, version, dsl_text, digest, published_by, now, now),
            )
            created = conn.execute(
                "SELECT * FROM workflow_versions WHERE id = ?", (vid,)
            ).fetchone()
            return dict(created)

    def get_version(self, workflow_id: str, version: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_versions WHERE workflow_id = ? AND version = ?",
                (workflow_id, version),
            ).fetchone()
        return dict(row) if row else None

    def get_version_by_id(self, version_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_versions WHERE id = ?", (version_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_versions(self, workflow_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_versions WHERE workflow_id = ? ORDER BY version",
                (workflow_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def sync_triggers(self, workflow_id: str, triggers: list) -> list[dict]:
        """Project a workflow's triggers into `workflow_triggers` (idempotent)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM workflow_triggers WHERE workflow_id = ?", (workflow_id,))
            for trigger in triggers or []:
                if not isinstance(trigger, dict) or len(trigger) != 1:
                    continue
                kind, value = next(iter(trigger.items()))
                conn.execute(
                    "INSERT INTO workflow_triggers (workflow_id, kind, value)"
                    " VALUES (?, ?, ?)"
                    " ON CONFLICT(workflow_id, kind, value) DO NOTHING",
                    (workflow_id, str(kind), "" if value is True else str(value)),
                )
            rows = conn.execute(
                "SELECT kind, value FROM workflow_triggers WHERE workflow_id = ?"
                " ORDER BY kind, value",
                (workflow_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_triggers(self, workflow_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, value FROM workflow_triggers WHERE workflow_id = ?"
                " ORDER BY kind, value",
                (workflow_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def publish(
        self,
        workflow,
        dsl_text: str | None = None,
        *,
        workspace_id: str = "",
        created_by: str = "",
    ) -> dict:
        """Persist a loaded workflow, its pinned version and its triggers."""
        text = dsl_text if dsl_text is not None else getattr(workflow, "raw_text", "")
        workflow_id = self.ensure_workflow(
            getattr(workflow, "name", ""), workspace_id=workspace_id, created_by=created_by
        )
        row = self.publish_version(
            workflow_id, getattr(workflow, "version", 0), text, published_by=created_by
        )
        self.sync_triggers(workflow_id, getattr(workflow, "triggers", []))
        return row


class ExecutionStore:
    """Execution + per-node step persistence (item [28], FR-05/FR-10).

    Every execution references one exact ``(workflow_id, version)``. Steps
    are keyed by ``(execution_id, node_id, attempt)`` so re-recording an
    attempt updates rather than duplicates (idempotent persistence before an
    event is acknowledged).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def start(
        self,
        workflow_id: str,
        version: int,
        *,
        triggered_by: str = "manual",
        run_id: str = "",
    ) -> dict:
        """Insert a RUNNING execution row; `run_id` pins the correlation id."""
        execution_id = run_id or new_id()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO executions (id, workflow_id, version, status, triggered_by,"
                " started_at, finished_at, error) VALUES (?, ?, ?, 'RUNNING', ?, ?, '', '')",
                (execution_id, workflow_id, version, triggered_by, utcnow()),
            )
            row = conn.execute("SELECT * FROM executions WHERE id = ?", (execution_id,)).fetchone()
        return dict(row)

    def finish(self, execution_id: str, status: str, error: str = "") -> None:
        """Mark an execution terminal with its final status and error."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE executions SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, utcnow(), error, execution_id),
            )

    def record_step(
        self,
        execution_id: str,
        node_id: str,
        state: str,
        *,
        attempt: int = 1,
        error: str = "",
        output_summary: str = "",
    ) -> dict:
        """Upsert one node attempt; returns the stored row."""
        now = utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM execution_steps WHERE execution_id = ? AND node_id = ?"
                " AND attempt = ?",
                (execution_id, node_id, attempt),
            ).fetchone()
            if row:
                step_id = str(row["id"])
                conn.execute(
                    "UPDATE execution_steps SET state = ?, error = ?, output_summary = ?,"
                    " updated_at = ? WHERE id = ?",
                    (state, error[:2000], output_summary, now, step_id),
                )
            else:
                step_id = new_id()
                conn.execute(
                    "INSERT INTO execution_steps (id, execution_id, node_id, state, attempt,"
                    " error, output_summary, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        step_id,
                        execution_id,
                        node_id,
                        state,
                        attempt,
                        error[:2000],
                        output_summary,
                        now,
                    ),
                )
            step = conn.execute(
                "SELECT * FROM execution_steps WHERE id = ?", (step_id,)
            ).fetchone()
        return dict(step)

    def list_steps(self, execution_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_steps WHERE execution_id = ? ORDER BY rowid",
                (execution_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, execution_id: str) -> dict:
        """One execution row plus its ordered steps."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM executions WHERE id = ?", (execution_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown execution id: {execution_id}")
        payload = dict(row)
        payload["steps"] = self.list_steps(execution_id)
        return payload

    def record_run(
        self, workflow_id: str, version: int, run, *, triggered_by: str = "manual"
    ) -> dict:
        """Persist one completed executor RunResult: execution + node steps."""
        execution = self.start(
            workflow_id,
            version,
            triggered_by=triggered_by,
            run_id=str(getattr(run, "run_id", "") or ""),
        )
        order = list(getattr(run, "order", []) or [])
        nodes = getattr(run, "nodes", {}) or {}
        for node_id in [*order, *[nid for nid in nodes if nid not in order]]:
            node = nodes[node_id]
            self.record_step(
                execution["id"],
                node_id,
                state_for(str(getattr(node, "status", ""))),
                attempt=max(1, int(getattr(node, "attempts", 1) or 1)),
                error=str(getattr(node, "error", "") or ""),
                output_summary=summarize_output(getattr(node, "output", {}) or {}),
            )
        self.finish(
            execution["id"],
            str(getattr(run, "status", "FAILED")),
            str(getattr(run, "error", "") or ""),
        )
        return self.get(execution["id"])
