"""Phase 7 item [64]: explicit consent records and revocation (FR-14).

A memory item can only be persisted while a *live* consent exists for its
subject and scope. Consent is a first-class row, not a flag on the item:
granting is idempotent, revoking stamps ``revoked_at`` (the record is kept,
never deleted, so the history stays auditable), and re-granting revives the
same row. Erasure (item [71]) deletes the *items*, the consent history stays.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .ids import new_id

#: The scope a caller must hold before a plan may be stored as memory.
SCOPE_MEMORY_STORE = "memory:store"
#: Scopes the MVP knows about (unknown scopes are rejected, not ignored).
SCOPES: tuple[str, ...] = (SCOPE_MEMORY_STORE, "memory:reuse")


def utcnow() -> str:
    """ISO-8601 UTC timestamp (matches the rest of the persistence layer)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConsentError(RuntimeError):
    """An operation the consent store forbids (unknown scope, missing consent)."""


@dataclass(frozen=True)
class Consent:
    """One subject/scope consent record."""

    id: str
    subject: str
    scope: str
    granted_at: str
    revoked_at: str = ""

    @property
    def active(self) -> bool:
        return not self.revoked_at


def _to_consent(row: dict) -> Consent:
    return Consent(
        id=str(row["id"]),
        subject=str(row["subject"]),
        scope=str(row["scope"]),
        granted_at=str(row["granted_at"]),
        revoked_at=str(row["revoked_at"] or ""),
    )


class ConsentStore:
    """sqlite-backed consent records (grant, revoke, check, list)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def grant(
        self, subject: str, scope: str = SCOPE_MEMORY_STORE, *, now: str | None = None
    ) -> Consent:
        """Grant (or re-grant) consent for one subject/scope."""
        self._validate(subject, scope)
        moment = now or utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM consents WHERE subject = ? AND scope = ?", (subject, scope)
            ).fetchone()
            if row:
                conn.execute("UPDATE consents SET revoked_at = '' WHERE id = ?", (row["id"],))
                updated = conn.execute(
                    "SELECT * FROM consents WHERE id = ?", (row["id"],)
                ).fetchone()
                return _to_consent(dict(updated))
            conn.execute(
                "INSERT INTO consents (id, subject, scope, granted_at, revoked_at)"
                " VALUES (?, ?, ?, ?, '')",
                (new_id(), subject, scope, moment),
            )
            created = conn.execute(
                "SELECT * FROM consents WHERE subject = ? AND scope = ?", (subject, scope)
            ).fetchone()
        return _to_consent(dict(created))

    def revoke(
        self, subject: str, scope: str = SCOPE_MEMORY_STORE, *, now: str | None = None
    ) -> Consent:
        """Revoke consent; the record is kept for audit history."""
        self._validate(subject, scope)
        moment = now or utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM consents WHERE subject = ? AND scope = ?", (subject, scope)
            ).fetchone()
            if row is None:
                raise KeyError(f"no consent for {subject!r}/{scope!r}")
            conn.execute("UPDATE consents SET revoked_at = ? WHERE id = ?", (moment, row["id"]))
            updated = conn.execute("SELECT * FROM consents WHERE id = ?", (row["id"],)).fetchone()
        return _to_consent(dict(updated))

    def is_granted(self, subject: str, scope: str = SCOPE_MEMORY_STORE) -> bool:
        """True only while a live (non-revoked) consent exists."""
        self._validate(subject, scope)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revoked_at FROM consents WHERE subject = ? AND scope = ?",
                (subject, scope),
            ).fetchone()
        return row is not None and not str(row["revoked_at"] or "")

    def require(self, subject: str, scope: str = SCOPE_MEMORY_STORE) -> Consent:
        """Return the live consent or raise :class:`ConsentError` (fail closed)."""
        self._validate(subject, scope)
        if not self.is_granted(subject, scope):
            raise ConsentError(f"no active consent for {subject!r} scope {scope!r}")
        return self.get(subject, scope)

    def get(self, subject: str, scope: str = SCOPE_MEMORY_STORE) -> Consent:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM consents WHERE subject = ? AND scope = ?", (subject, scope)
            ).fetchone()
        if row is None:
            raise KeyError(f"no consent for {subject!r}/{scope!r}")
        return _to_consent(dict(row))

    def list(self, subject: str = "") -> list[Consent]:
        """Consents for one subject (all subjects when empty)."""
        query = "SELECT * FROM consents"
        values: list[object] = []
        if subject:
            query += " WHERE subject = ?"
            values.append(subject)
        query += " ORDER BY subject, scope"
        with self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [_to_consent(dict(row)) for row in rows]

    @staticmethod
    def _validate(subject: str, scope: str) -> None:
        if not str(subject or "").strip():
            raise ConsentError("consent subject must be non-empty")
        if scope not in SCOPES:
            raise ConsentError(f"unknown consent scope {scope!r}, expected one of {list(SCOPES)}")
