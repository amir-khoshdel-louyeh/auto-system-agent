"""Bus event model: 5 frozen contract types, every event idempotent (stdlib-only).

Frozen cross-service contract (plan.tmp phase 3):
`plan.created`, `approval.requested`, `step.failed`, `file.arrived`,
`schedule.tick`. Unknown types are rejected at construction — the freeze is
enforced in code, not docs.

Worker-internal step lifecycle (`step.dispatched`, `step.finished`) lives in
the `step.*` namespace the proposal (§6 C9) reserves for the engine; it is
not part of the cross-service contract and never leaves the `steps:*`
streams.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: Frozen cross-service contract. New types = new proposal, not silent expansion.
FROZEN_TYPES: tuple[str, ...] = (
    "plan.created",
    "approval.requested",
    "step.failed",
    "file.arrived",
    "schedule.tick",
)

#: Engine-internal step lifecycle (proposal §6 C9 `step.*` family).
WORKER_STEP_TYPES: tuple[str, ...] = (
    "step.dispatched",
    "step.finished",
)

KNOWN_TYPES: frozenset[str] = frozenset(FROZEN_TYPES + WORKER_STEP_TYPES)


def utcnow() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Event:
    """One bus event; `idempotency_key` dedupes redeliveries and replays."""

    id: str
    type: str
    source: str
    time: str
    data: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if self.type not in KNOWN_TYPES:
            raise ValueError(
                f"unknown event type {self.type!r}; frozen set is {sorted(KNOWN_TYPES)}"
            )
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required on every event")

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form for broker serialization."""
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "time": self.time,
            "data": self.data,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
        }

    def to_json(self) -> str:
        """Canonical JSON (sorted keys) for broker payloads."""
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @staticmethod
    def from_dict(obj: dict[str, Any]) -> Event:
        """Rebuild an event; raises ValueError on unknown types."""
        data = obj.get("data", {})
        return Event(
            id=str(obj.get("id", "")),
            type=str(obj.get("type", "")),
            source=str(obj.get("source", "")),
            time=str(obj.get("time", "")),
            data=data if isinstance(data, dict) else {},
            idempotency_key=str(obj.get("idempotency_key", "")),
            correlation_id=str(obj.get("correlation_id", "")),
        )


def new_event(
    type: str,
    data: dict[str, Any] | None = None,
    *,
    source: str = "engine",
    correlation_id: str = "",
    idempotency_key: str = "",
) -> Event:
    """Build an event; default idempotency key is unique per event."""
    return Event(
        id=uuid.uuid4().hex,
        type=type,
        source=source,
        time=utcnow(),
        data=dict(data or {}),
        idempotency_key=idempotency_key or f"{type}:{uuid.uuid4().hex}",
        correlation_id=correlation_id,
    )
