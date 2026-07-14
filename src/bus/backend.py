"""Bus backends: ABC plus ephemeral in-memory Streams (adapted, src-only).

Mirrors the shape of source_MIT/EventFlow `core/broker.py::MessageBroker`
(publish / consumer-group subscribe / acknowledge / pending count) with
deliberate deltas: no redis/structlog/pydantic imports here, plus our own
idempotency dedupe (same key → same message id, no duplicate append) and
range reads for replay. The Redis Streams backend lives in `redis_backend.py`
and implements this same ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .events import Event


class Backend(ABC):
    """Broker contract both backends implement."""

    @abstractmethod
    async def publish(self, stream: str, event: Event) -> str:
        """Append (or dedupe by idempotency key); returns the message id."""

    @abstractmethod
    async def ensure_group(self, stream: str, group: str) -> None:
        """Create a consumer group idempotently (reads from the beginning)."""

    @abstractmethod
    async def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[tuple[str, Event]]:
        """New messages for one group; delivered entries become pending."""

    @abstractmethod
    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        """Mark one delivery done."""

    @abstractmethod
    async def pending_count(self, stream: str, group: str) -> int:
        """Unacknowledged deliveries for one group."""

    @abstractmethod
    async def read_range(
        self, stream: str, start: int = 0, end: int | None = None
    ) -> list[tuple[str, Event]]:
        """Full ordered slice for replay (acks untouched)."""

    @abstractmethod
    async def to_dlq(self, stream: str, event: Event, reason: str) -> str:
        """Park an exhausted event; returns the DLQ message id."""

    @abstractmethod
    async def dlq_list(self, stream: str) -> list[tuple[str, Event, str]]:
        """(message id, event, reason) parked for one stream."""


class MemoryBackend(Backend):
    """Ephemeral Streams clone for tests and local runs (no server needed)."""

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, Event]]] = {}
        self._keys: dict[str, dict[str, str]] = {}
        self._cursors: dict[tuple[str, str], int] = {}
        self._pending: dict[tuple[str, str], dict[str, Event]] = {}
        self._dlq: dict[str, list[tuple[str, Event, str]]] = {}
        self._seq = 0

    def reset(self) -> None:
        """Drop everything (ephemeral between tests)."""
        self._streams.clear()
        self._keys.clear()
        self._cursors.clear()
        self._pending.clear()
        self._dlq.clear()
        self._seq = 0

    async def publish(self, stream: str, event: Event) -> str:
        known = self._keys.setdefault(stream, {})
        if event.idempotency_key in known:
            return known[event.idempotency_key]
        self._seq += 1
        message_id = f"{self._seq}-0"
        self._streams.setdefault(stream, []).append((message_id, event))
        known[event.idempotency_key] = message_id
        return message_id

    async def ensure_group(self, stream: str, group: str) -> None:
        self._streams.setdefault(stream, [])
        self._cursors.setdefault((stream, group), 0)
        self._pending.setdefault((stream, group), {})

    async def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[tuple[str, Event]]:
        await self.ensure_group(stream, group)
        cursor = self._cursors[(stream, group)]
        entries = self._streams.get(stream, [])
        fresh = [(mid, evt) for mid, evt in entries if int(mid.split("-")[0]) > cursor]
        batch = fresh[:count]
        if batch:
            self._cursors[(stream, group)] = int(batch[-1][0].split("-")[0])
            pending = self._pending[(stream, group)]
            for mid, evt in batch:
                pending[mid] = evt
        return batch

    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        self._pending.get((stream, group), {}).pop(message_id, None)

    async def pending_count(self, stream: str, group: str) -> int:
        return len(self._pending.get((stream, group), {}))

    async def read_range(
        self, stream: str, start: int = 0, end: int | None = None
    ) -> list[tuple[str, Event]]:
        entries = self._streams.get(stream, [])
        return [(mid, evt) for mid, evt in entries if start <= int(mid.split("-")[0]) and (end is None or int(mid.split("-")[0]) <= end)]

    async def to_dlq(self, stream: str, event: Event, reason: str) -> str:
        self._seq += 1
        message_id = f"{self._seq}-0"
        self._dlq.setdefault(stream, []).append((message_id, event, reason))
        return message_id

    async def dlq_list(self, stream: str) -> list[tuple[str, Event, str]]:
        return list(self._dlq.get(stream, []))
