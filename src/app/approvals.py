"""Approval state machine: request, quorum, expiry, final decision (item [35]).

Owns FR-08. An approval node creates one ``approval_requests`` row carrying
the required quorum and a deadline; each approver writes an ``approvals``
decision row. The request resolves to ``APPROVED`` when the number of
distinct approvers reaches the quorum, to ``REJECTED`` on any rejection, to
``EXPIRED`` when the deadline passes while still pending, and to
``CANCELLED`` when the run is aborted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.audit import AuditStore, AuditTrail

from .models import new_id, utcnow

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"

#: Verdicts a caller may cast.
APPROVE = "APPROVED"
REJECT = "REJECTED"

_TERMINAL = frozenset({APPROVED, REJECTED, EXPIRED, CANCELLED})


class ApprovalError(RuntimeError):
    """An operation the approval state machine forbids."""


@dataclass(frozen=True)
class ApprovalRequest:
    """One node's approval request."""

    id: str
    execution_id: str
    node_id: str
    quorum: int
    status: str
    expires_at: str
    created_at: str


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _is_expired(expires_at: str, now: str) -> bool:
    if not expires_at:
        return False
    return _parse(expires_at) <= _parse(now)


class ApprovalService:
    """sqlite-backed approval request + decision store.

    With an ``audit`` store every decision and expiry is also appended to
    the execution's hash chain (FR-12: every approval has an auditable
    record). The chain is keyed by the execution id, so a decision is
    tamper-evident next to the run it authorises.
    """

    def __init__(self, path: str | Path, audit: AuditStore | None = None) -> None:
        self.path = str(path)
        self.audit = audit

    def _audit(self, execution_id: str):
        """Audit trail for one execution, or None when auditing is off."""
        if self.audit is None:
            return None
        return AuditTrail(self.audit, execution_id)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- lifecycle --------------------------------------------------------
    def request(
        self,
        execution_id: str,
        node_id: str,
        *,
        quorum: int = 1,
        ttl_seconds: int = 3600,
        now: str | None = None,
    ) -> ApprovalRequest:
        """Create (or return the existing) PENDING approval request."""
        if quorum < 1:
            raise ValueError("quorum must be >= 1")
        created = now or utcnow()
        expires = _format(_parse(created) + timedelta(seconds=ttl_seconds))
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE execution_id = ? AND node_id = ?",
                (execution_id, node_id),
            ).fetchone()
            if row:
                return _to_request(dict(row))
            request_id = new_id()
            conn.execute(
                "INSERT INTO approval_requests (id, execution_id, node_id, quorum, status,"
                " expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, execution_id, node_id, int(quorum), PENDING, expires, created),
            )
            created_row = conn.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return _to_request(dict(created_row))

    def decide(
        self,
        execution_id: str,
        node_id: str,
        *,
        approver: str,
        verdict: str,
        reason: str = "",
        now: str | None = None,
    ) -> ApprovalRequest:
        """Record one decision; resolve the request when quorum/rejection hits."""
        moment = now or utcnow()
        expired_request: ApprovalRequest | None = None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE execution_id = ? AND node_id = ?",
                (execution_id, node_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"no approval request for {execution_id}:{node_id}")
            request = _to_request(dict(row))
            if request.status in _TERMINAL:
                raise ApprovalError(f"approval is already {request.status}")
            if _is_expired(request.expires_at, moment):
                # Persist EXPIRED, then report it *after* the transaction
                # commits: raising inside the block would roll the update back.
                conn.execute(
                    "UPDATE approval_requests SET status = ? WHERE id = ?",
                    (EXPIRED, request.id),
                )
                expired_request = request
            else:
                normalized = verdict.upper()
                if normalized not in (APPROVE, REJECT):
                    raise ValueError("verdict must be APPROVED or REJECTED")
                conn.execute(
                    "INSERT INTO approvals (id, execution_id, node_id, verdict, decided_by,"
                    " decided_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), execution_id, node_id, normalized, approver, moment, reason),
                )
                distinct = conn.execute(
                    "SELECT COUNT(DISTINCT decided_by) AS n FROM approvals"
                    " WHERE execution_id = ? AND node_id = ? AND verdict = ?",
                    (execution_id, node_id, APPROVE),
                ).fetchone()["n"]
                rejected = conn.execute(
                    "SELECT COUNT(*) AS n FROM approvals"
                    " WHERE execution_id = ? AND node_id = ? AND verdict = ?",
                    (execution_id, node_id, REJECT),
                ).fetchone()["n"]
                if rejected:
                    status = REJECTED
                elif distinct >= request.quorum:
                    status = APPROVED
                else:
                    status = PENDING
                conn.execute(
                    "UPDATE approval_requests SET status = ? WHERE id = ?", (status, request.id)
                )
                updated = conn.execute(
                    "SELECT * FROM approval_requests WHERE id = ?", (request.id,)
                ).fetchone()
                request = _to_request(dict(updated))
        if expired_request is not None:
            trail = self._audit(execution_id)
            if trail is not None:
                trail.approval(node_id, EXPIRED, reason="expired before the decision")
            raise ApprovalError("approval request expired")
        trail = self._audit(execution_id)
        if trail is not None:
            trail.approval(
                node_id,
                request.status,
                decided_by=approver,
                reason=reason or verdict.upper(),
            )
        return request

    def cancel(self, execution_id: str, node_id: str) -> ApprovalRequest:
        """Cancel a pending request (run aborted)."""
        return self._set_status(execution_id, node_id, CANCELLED, allow=(PENDING,))

    def sweep_expired(self, *, now: str | None = None) -> list[ApprovalRequest]:
        """Mark every overdue PENDING request EXPIRED; returns the changed rows."""
        moment = now or utcnow()
        expired: list[ApprovalRequest] = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM approval_requests WHERE status = ?", (PENDING,)
            ).fetchall()
            for row in rows:
                request = _to_request(dict(row))
                if _is_expired(request.expires_at, moment):
                    conn.execute(
                        "UPDATE approval_requests SET status = ? WHERE id = ?",
                        (EXPIRED, request.id),
                    )
                    updated = conn.execute(
                        "SELECT * FROM approval_requests WHERE id = ?", (request.id,)
                    ).fetchone()
                    expired.append(_to_request(dict(updated)))
        for request in expired:
            trail = self._audit(request.execution_id)
            if trail is not None:
                trail.approval(request.node_id, EXPIRED, reason="expired while pending")
        return expired

    # -- reads ------------------------------------------------------------
    def get(self, execution_id: str, node_id: str) -> ApprovalRequest:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE execution_id = ? AND node_id = ?",
                (execution_id, node_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"no approval request for {execution_id}:{node_id}")
        return _to_request(dict(row))

    def status(self, execution_id: str, node_id: str) -> str:
        return self.get(execution_id, node_id).status

    def list_requests(self, execution_id: str = "") -> list[ApprovalRequest]:
        query = "SELECT * FROM approval_requests"
        values: list[object] = []
        if execution_id:
            query += " WHERE execution_id = ?"
            values.append(execution_id)
        query += " ORDER BY created_at, node_id"
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [_to_request(dict(r)) for r in rows]

    def all_decisions(self, *, limit: int = 10000) -> list[dict]:
        """Every decision row across requests (metrics)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT execution_id, node_id, verdict, decided_by, decided_at, reason"
                " FROM approvals ORDER BY decided_at LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def decisions(self, execution_id: str, node_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT verdict, decided_by, decided_at, reason FROM approvals"
                " WHERE execution_id = ? AND node_id = ? ORDER BY decided_at, decided_by",
                (execution_id, node_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- internals --------------------------------------------------------
    def _set_status(
        self, execution_id: str, node_id: str, status: str, *, allow: tuple[str, ...]
    ) -> ApprovalRequest:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE execution_id = ? AND node_id = ?",
                (execution_id, node_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"no approval request for {execution_id}:{node_id}")
            request = _to_request(dict(row))
            if request.status not in allow:
                raise ApprovalError(f"approval is already {request.status}")
            conn.execute(
                "UPDATE approval_requests SET status = ? WHERE id = ?", (status, request.id)
            )
            updated = conn.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request.id,)
            ).fetchone()
        return _to_request(dict(updated))


def _to_request(row: dict) -> ApprovalRequest:
    return ApprovalRequest(
        id=row["id"],
        execution_id=row["execution_id"],
        node_id=row["node_id"],
        quorum=int(row["quorum"]),
        status=row["status"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


def _format(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
