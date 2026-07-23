"""Redis Streams backend: the same ABC over the compose redis (adapted).

Mirrors the shape of source_MIT/EventFlow `core/broker.py::RedisStreamBroker`
(XADD publish, MKSTREAM group creation, XREADGROUP/XACK consume, XPENDING
count) with deliberate deltas: one JSON `payload` field instead of pydantic
models, idempotency dedupe via `SET NX` (upstream has none), DLQ as a plain
sibling stream, and no structlog. `redis` imports lazily so the stdlib-only
paths never pay for it.
"""

from __future__ import annotations

import json
from typing import Any

from .backend import Backend
from .events import Event

_PAYLOAD_FIELD = "payload"
_IDEM_PREFIX = "bus:idem:"
_IDEM_TTL_S = 7 * 24 * 3600


class RedisBackend(Backend):
    """Streams broker over `redis.asyncio` (compose `redis:7`)."""

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        self.url = url
        self._client: Any = None

    async def _get_client(self):
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.Redis.from_url(self.url, decode_responses=True)
            await self._client.ping()
        return self._client

    async def close(self) -> None:
        """Release the connection (tests + worker shutdown)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _idem_key(self, stream: str, event: Event) -> str:
        return f"{_IDEM_PREFIX}{stream}:{event.idempotency_key}"

    async def publish(self, stream: str, event: Event) -> str:
        client = await self._get_client()
        key = self._idem_key(stream, event)
        if await client.set(key, "", nx=True, ex=_IDEM_TTL_S) is None:
            existing = await client.get(key)
            if existing:
                return existing
        message_id = await client.xadd(stream, {_PAYLOAD_FIELD: event.to_json()})
        await client.set(key, message_id, ex=_IDEM_TTL_S)
        return message_id

    async def ensure_group(self, stream: str, group: str) -> None:
        client = await self._get_client()
        try:
            await client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001 - BUSYGROUP means "already exists"
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_group(
        self, stream: str, group: str, consumer: str, count: int = 10
    ) -> list[tuple[str, Event]]:
        client = await self._get_client()
        await self.ensure_group(stream, group)
        raw = await client.xreadgroup(group, consumer, {stream: ">"}, count=count, block=0)
        out: list[tuple[str, Event]] = []
        for _, entries in raw or []:
            for message_id, fields in entries:
                out.append((message_id, Event.from_dict(json.loads(fields[_PAYLOAD_FIELD]))))
        return out

    async def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        client = await self._get_client()
        await client.xack(stream, group, message_id)

    async def pending_count(self, stream: str, group: str) -> int:
        client = await self._get_client()
        try:
            info = await client.xpending(stream, group)
            return int(info.get("pending", 0)) if info else 0
        except Exception:  # noqa: BLE001 - missing stream/group means "nothing pending"
            return 0

    async def read_range(
        self, stream: str, start: int = 0, end: int | None = None
    ) -> list[tuple[str, Event]]:
        client = await self._get_client()
        raw = await client.xrange(
            stream, min=f"{start}-0", max=f"{end}-0" if end is not None else "+"
        )
        return [
            (message_id, Event.from_dict(json.loads(fields[_PAYLOAD_FIELD])))
            for message_id, fields in raw
        ]

    async def to_dlq(self, stream: str, event: Event, reason: str) -> str:
        client = await self._get_client()
        return await client.xadd(
            f"{stream}:dlq", {_PAYLOAD_FIELD: event.to_json(), "reason": reason}
        )

    async def dlq_list(self, stream: str) -> list[tuple[str, Event, str]]:
        client = await self._get_client()
        raw = await client.xrange(f"{stream}:dlq", min="-", max="+")
        return [
            (
                message_id,
                Event.from_dict(json.loads(fields[_PAYLOAD_FIELD])),
                fields.get("reason", ""),
            )
            for message_id, fields in raw
        ]
