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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


def utcnow() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_payload(payload: Any) -> str:
    """Canonical JSON (sorted keys) — the exact bytes that get hashed.

    Two logically equal payloads always serialise identically regardless of
    insertion order, so a hash mismatch can only mean the content changed.
    """
    return json.dumps(payload or {}, sort_keys=True, default=str)


@dataclass(frozen=True)
class ChainVerification:
    """Result of verifying one audit chain (FR-12).

    ``first_broken_seq`` pinpoints the earliest tampered/missing row instead
    of returning a generic failure.
    """

    ok: bool
    checked: int
    first_broken_seq: int | None = None
    reason: str = ""


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
        payload_json = canonical_payload(payload)
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
        return self.verify_chain(execution_id).ok

    def verify_chain(self, execution_id: str) -> ChainVerification:
        """Verify one chain, reporting the *first* broken link (FR-12)."""
        prev = GENESIS_HASH
        checked = 0
        for row in self.events(execution_id):
            if row["prev_hash"] != prev:
                return ChainVerification(
                    ok=False,
                    checked=checked,
                    first_broken_seq=row["seq"],
                    reason="previous hash does not match the prior row",
                )
            recomputed = chain_hash(prev, row["seq"], row["event"], row["payload"])
            if row["hash"] != recomputed:
                return ChainVerification(
                    ok=False,
                    checked=checked,
                    first_broken_seq=row["seq"],
                    reason="row hash does not match its content",
                )
            prev = row["hash"]
            checked += 1
        return ChainVerification(ok=True, checked=checked)


#: Audit event vocabulary (FR-12): every category the chain must record.
RUN_STARTED = "run.started"
RUN_FINISHED = "run.finished"
POLICY_DECISION = "policy.decision"
APPROVAL_DECISION = "approval.decision"
NODE_DISPATCHED = "node.dispatched"
NODE_RETRY = "node.retry"
NODE_RESULT = "node.result"
RUN_REPLAY = "run.replay"


def _decision_payload(decision: Any) -> dict[str, Any]:
    """Normalise a policy decision (dataclass, dict or plain object)."""
    if hasattr(decision, "as_dict"):
        return dict(decision.as_dict())
    if isinstance(decision, dict):
        return dict(decision)
    return {"verdict": str(decision)}


class AuditTrail:
    """Records the FR-12 event vocabulary onto one execution's hash chain.

    Callers pass *summaries*, never raw stdout or secrets; each method appends
    one canonical-payload row so the chain stays verifiable.
    """

    def __init__(self, store: AuditStore, execution_id: str) -> None:
        self.store = store
        self.execution_id = execution_id

    def _append(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.store.append(self.execution_id, event, payload)

    def record(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one raw event (used for events with no dedicated helper)."""
        return self._append(event, payload)

    def run_started(
        self, workflow: str, version: int, *, trigger: str = "manual", run_id: str = ""
    ) -> dict[str, Any]:
        return self._append(
            RUN_STARTED,
            {"run_id": run_id, "workflow": workflow, "version": version, "trigger": trigger},
        )

    def run_finished(self, status: str, error: str = "") -> dict[str, Any]:
        return self._append(RUN_FINISHED, {"status": status, "error": error[:2000]})

    def policy(self, node_id: str, decision: Any) -> dict[str, Any]:
        payload = _decision_payload(decision)
        payload.setdefault("node_id", node_id)
        return self._append(POLICY_DECISION, payload)

    def approval(
        self, node_id: str, status: str, *, decided_by: str = "", reason: str = ""
    ) -> dict[str, Any]:
        return self._append(
            APPROVAL_DECISION,
            {"node_id": node_id, "status": status, "decided_by": decided_by, "reason": reason},
        )

    def dispatch(self, node_id: str, *, run_id: str = "") -> dict[str, Any]:
        return self._append(NODE_DISPATCHED, {"run_id": run_id, "node_id": node_id})

    def retry(self, node_id: str, attempt: int, error: str = "", *, run_id: str = "") -> dict:
        return self._append(
            NODE_RETRY,
            {"run_id": run_id, "node_id": node_id, "attempt": attempt, "error": error[:2000]},
        )

    def result(
        self, node_id: str, status: str, *, output_summary: str = "", run_id: str = ""
    ) -> dict[str, Any]:
        return self._append(
            NODE_RESULT,
            {
                "run_id": run_id,
                "node_id": node_id,
                "status": status,
                "output_summary": output_summary[:2000],
            },
        )

    def replay(
        self, source_execution_id: str, replay_run_id: str, *, match: bool
    ) -> dict[str, Any]:
        return self._append(
            RUN_REPLAY,
            {
                "source_execution_id": source_execution_id,
                "replay_run_id": replay_run_id,
                "match": match,
            },
        )

    def sink(self):
        """A scheduler `on_event` callback writing every emitted event row."""

        def on_event(name: str, payload: dict[str, Any]) -> None:
            self._append(name, payload)

        return on_event


def audit_sink(store: AuditStore, execution_id: str):
    """Build a scheduler `on_event` callback that audits run/node events."""
    return AuditTrail(store, execution_id).sink()
