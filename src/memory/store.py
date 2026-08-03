"""Phase 7 item [70]: the memory store — consent, scrub, quarantine, states.

One write path, three guards, in this order:

1. **consent** — a live `memory:store` consent must exist for the subject
   (item [64]); without it nothing is persisted;
2. **scrub** — the canonical plan is PII-scrubbed (item [65]) and the store
   refuses to write if any rule still matches afterwards (fail closed);
3. **fingerprint** — one row per canonical plan, so re-saving an identical
   plan is idempotent instead of duplicating memory.

A stored plan starts ``QUARANTINED`` with reputation 20 and only moves
through ``SANDBOX`` to ``PUBLIC`` when :func:`memory.states.next_state`
allows it; a failed or denied episode puts it back in quarantine, so a
poisoned plan can never be auto-reused (Phase 7 gate).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .consent import SCOPE_MEMORY_STORE, ConsentStore
from .embeddings import EMBEDDING_MODEL, embed, serialize
from .ids import canonical_json, fingerprint, new_id, utcnow
from .reputation import ReputationState
from .scrub import contains_pii, scrub_plan
from .states import (
    ALL_STATES,
    QUARANTINED,
    next_state,
    require_transition,
)

#: Risk labels accepted for a memory item.
RISK_LEVELS: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "UNSAFE")


class MemoryError(RuntimeError):
    """A memory operation the store refuses (no consent, PII, bad input)."""


@dataclass(frozen=True)
class MemoryItem:
    """One stored plan (canonical, scrubbed, embedded and scored)."""

    id: str
    fingerprint: str
    intent: str
    canonical_plan: str
    summary: str
    embedding: str
    pii_findings: int
    risk: str
    os_name: str
    state: str
    reputation: float
    reuse_count: int
    success_count: int
    rating_sum: float
    rating_count: int
    created_by: str
    created_at: str
    updated_at: str

    def plan(self) -> Any:
        """Decoded canonical plan."""
        return json.loads(self.canonical_plan)

    @property
    def reusable(self) -> bool:
        from .states import is_reusable

        return is_reusable(self.state)

    def reputation_state(self) -> ReputationState:
        return ReputationState(
            reuse_count=self.reuse_count,
            success_count=self.success_count,
            rating_sum=self.rating_sum,
            rating_count=self.rating_count,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fingerprint": self.fingerprint,
            "intent": self.intent,
            "summary": self.summary,
            "risk": self.risk,
            "os_name": self.os_name,
            "state": self.state,
            "reputation": self.reputation,
            "reuse_count": self.reuse_count,
            "success_count": self.success_count,
            "rating_count": self.rating_count,
            "pii_findings": self.pii_findings,
            "reusable": self.reusable,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _to_item(row: dict) -> MemoryItem:
    return MemoryItem(
        id=str(row["id"]),
        fingerprint=str(row["fingerprint"]),
        intent=str(row["intent"]),
        canonical_plan=str(row["canonical_plan"]),
        summary=str(row["summary"]),
        embedding=str(row["embedding"]),
        pii_findings=int(row["pii_findings"]),
        risk=str(row["risk"]),
        os_name=str(row["os_name"]),
        state=str(row["state"]),
        reputation=float(row["reputation"]),
        reuse_count=int(row["reuse_count"]),
        success_count=int(row["success_count"]),
        rating_sum=float(row["rating_sum"]),
        rating_count=int(row["rating_count"]),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class MemoryStore:
    """sqlite-backed shared memory (FR-14/FR-15)."""

    def __init__(self, path: str | Path, *, consents: ConsentStore | None = None) -> None:
        self.path = str(path)
        self.consents = consents if consents is not None else ConsentStore(self.path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- writes -----------------------------------------------------------
    def save(
        self,
        plan: Any,
        *,
        subject: str,
        summary: str = "",
        intent: str = "",
        risk: str = "LOW",
        os_name: str = "",
        created_by: str = "",
    ) -> MemoryItem:
        """Store one canonical plan for a consenting subject (idempotent)."""
        self.consents.require(subject, SCOPE_MEMORY_STORE)
        if risk not in RISK_LEVELS:
            raise MemoryError(f"unknown risk {risk!r}, expected one of {list(RISK_LEVELS)}")
        if not isinstance(plan, (dict, list)) or not plan:
            raise MemoryError("plan must be a non-empty mapping or list")

        scrubbed, result = scrub_plan(plan)
        canonical = canonical_json(scrubbed)
        if contains_pii(canonical):
            raise MemoryError("plan still contains PII after scrubbing; refusing to store")
        digest = fingerprint(scrubbed)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM memory_items WHERE fingerprint = ?", (digest,)
            ).fetchone()
            if existing:
                return _to_item(dict(existing))

            moment = utcnow()
            item_id = new_id()
            payload = {
                "id": item_id,
                "fingerprint": digest,
                "intent": str(intent),
                "canonical_plan": canonical,
                "summary": str(summary)[:2000],
                "embedding": serialize(embed(f"{summary} {canonical}")),
                "pii_findings": result.count,
                "risk": risk,
                "os_name": str(os_name),
                "state": QUARANTINED,
                "reputation": ReputationState().reputation,
                "reuse_count": 0,
                "success_count": 0,
                "rating_sum": 0.0,
                "rating_count": 0,
                "created_by": str(created_by or subject),
                "created_at": moment,
                "updated_at": moment,
            }
            conn.execute(
                "INSERT INTO memory_items (id, fingerprint, intent, canonical_plan, summary,"
                " embedding, pii_findings, risk, os_name, state, reputation, reuse_count,"
                " success_count, rating_sum, rating_count, created_by, created_at, updated_at)"
                " VALUES (:id, :fingerprint, :intent, :canonical_plan, :summary, :embedding,"
                " :pii_findings, :risk, :os_name, :state, :reputation, :reuse_count,"
                " :success_count, :rating_sum, :rating_count, :created_by, :created_at,"
                " :updated_at)",
                payload,
            )
            self._event(conn, item_id, "created", f"{EMBEDDING_MODEL} findings={result.count}")
            created = conn.execute("SELECT * FROM memory_items WHERE id = ?", (item_id,)).fetchone()
        return _to_item(dict(created))

    def record_episode(
        self,
        item_id: str,
        *,
        success: bool,
        rating: float | None = None,
        detail: str = "sandbox run",
    ) -> MemoryItem:
        """Record one reuse/sandbox episode: counters, reputation, state."""
        item = self.get(item_id)
        state = item.reputation_state().apply(success=success, rating=rating)
        target = next_state(
            item.state,
            sandbox_proofs=state.success_count,
            reputation=state.reputation,
            last_outcome_ok=success,
        )
        require_transition(item.state, target)
        moment = utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_items SET state = ?, reputation = ?, reuse_count = ?,"
                " success_count = ?, rating_sum = ?, rating_count = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    target,
                    state.reputation,
                    state.reuse_count,
                    state.success_count,
                    state.rating_sum,
                    state.rating_count,
                    moment,
                    item_id,
                ),
            )
            self._event(
                conn,
                item_id,
                "reused" if success else "failed",
                f"{detail} -> {target} ({state.reputation:.2f})",
            )
            updated = conn.execute("SELECT * FROM memory_items WHERE id = ?", (item_id,)).fetchone()
        return _to_item(dict(updated))

    def erase(self, item_id: str) -> bool:
        """Delete one item and its events (the consent history stays)."""
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM memory_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM memory_events WHERE memory_id = ?", (item_id,))
            conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
        return True

    # -- reads ------------------------------------------------------------
    def get(self, item_id: str) -> MemoryItem:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown memory item {item_id!r}")
        return _to_item(dict(row))

    def by_fingerprint(self, digest: str) -> MemoryItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_items WHERE fingerprint = ?", (digest,)
            ).fetchone()
        return _to_item(dict(row)) if row else None

    def list_items(self, *, state: str = "", limit: int = 100) -> list[MemoryItem]:
        query = "SELECT * FROM memory_items"
        values: list[object] = []
        if state:
            if state not in ALL_STATES:
                raise MemoryError(f"unknown state {state!r}, expected one of {list(ALL_STATES)}")
            query += " WHERE state = ?"
            values.append(state)
        query += " ORDER BY reputation DESC, created_at LIMIT ?"
        values.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [_to_item(dict(row)) for row in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM memory_items").fetchone()["n"])

    def events(self, item_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_events WHERE memory_id = ? ORDER BY created_at, rowid",
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- internals --------------------------------------------------------
    @staticmethod
    def _event(conn: sqlite3.Connection, item_id: str, event: str, detail: str) -> None:
        conn.execute(
            "INSERT INTO memory_events (id, memory_id, event, detail, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (new_id(), item_id, event, detail[:2000], utcnow()),
        )
