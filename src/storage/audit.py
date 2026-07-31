"""Phase 2 audit store: append-only log with hash chain (A3, stdlib-only).

Every run, approval and step event is appended to `audit_events` (migration
002) with `hash = sha256(prev_hash | seq | event | payload)`. Tampering with
any row breaks `verify()`. Payloads must stay free of raw secrets/PII —
callers pass summaries, never full stdout.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


def utcnow() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def chain_hash(prev_hash: str, seq: int, event: str, payload_json: str) -> str:
    """Hash linking one audit row to its predecessor."""
    body = f"{prev_hash}|{seq}|{event}|{payload_json}".encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class AuditStore:
    """Append/verify audit events on a sqlite3 (or Postgres-compatible) db."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def append(self, execution_id: str, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one event; returns the stored row as a dict."""
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        with self._connect() as conn:
            last = conn.execute(
                "SELECT seq, hash FROM audit_events WHERE execution_id = ? ORDER BY seq DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
            seq = (last["seq"] + 1) if last else 0
            prev = last["hash"] if last else GENESIS_HASH
            digest = chain_hash(prev, seq, event, payload_json)
            row = {
                "id": uuid.uuid4().hex,
                "execution_id": execution_id,
                "seq": seq,
                "event": event,
                "payload": payload_json,
                "prev_hash": prev,
                "hash": digest,
                "created_at": utcnow(),
            }
            conn.execute(
                "INSERT INTO audit_events (id, execution_id, seq, event, payload,"
                " prev_hash, hash, created_at) VALUES (:id, :execution_id, :seq,"
                " :event, :payload, :prev_hash, :hash, :created_at)",
                row,
            )
            return row

    def events(self, execution_id: str) -> list[dict[str, Any]]:
        """All rows for one execution, ordered by seq."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE execution_id = ? ORDER BY seq",
                (execution_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def count(self, execution_id: str = "") -> int:
        """Number of audit rows for one execution (all chains when empty)."""
        with self._connect() as conn:
            if execution_id:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM audit_events WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()
        return int(row["n"])

    def list_page(
        self,
        *,
        execution_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """One page of rows, ordered by (execution_id, seq) for stable paging."""
        query = "SELECT * FROM audit_events"
        values: list[object] = []
        if execution_id:
            query += " WHERE execution_id = ?"
            values.append(execution_id)
        query += " ORDER BY execution_id, seq LIMIT ? OFFSET ?"
        values.extend([max(1, int(limit)), max(0, int(offset))])
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def verify(self, execution_id: str) -> bool:
        """True when the whole chain recomputes (genesis → tip)."""
        prev = GENESIS_HASH
        for row in self.events(execution_id):
            if row["prev_hash"] != prev:
                return False
            if row["hash"] != chain_hash(prev, row["seq"], row["event"], row["payload"]):
                return False
            prev = row["hash"]
        return True


def audit_sink(store: AuditStore, execution_id: str):
    """Build a scheduler `on_event` callback that audits run/node events."""

    def sink(name: str, payload: dict[str, Any]) -> None:
        store.append(execution_id, name, payload)

    return sink
