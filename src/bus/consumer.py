"""Consumer-group runner: retry with backoff, DLQ, idempotent handling.

Mirrors the shape of source_MIT/EventFlow `consumers/consumer.py`
(handler registry, `while retry <= max` loop, exponential backoff, DLQ park
+ ack) with deliberate deltas: no SQLAlchemy status updates (the audit
store from phase 2 is the observability trail) and no structlog. At-least-
once delivery + per-run seen-keys make redeliveries safe; replay re-reads a
range through the same handlers without touching acks.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .backend import Backend
from .events import Event

#: (event) -> None. Raises on processing failure.
Handler = Callable[[Event], Awaitable[None]]


@dataclass
class RunStats:
    """Counters for one drain/replay pass."""

    processed: int = 0
    errors: int = 0
    dlq: int = 0
    duplicates: int = 0
    backend_errors: int = 0


class Runner:
    """One consumer inside a group, over one backend."""

    def __init__(
        self,
        backend: Backend,
        group: str,
        streams: list[str],
        *,
        consumer_id: str = "",
        max_retries: int = 3,
        base_delay_ms: int = 100,
        max_delay_ms: int = 5000,
    ) -> None:
        self.backend = backend
        self.group = group
        self.streams = list(streams)
        self.consumer_id = consumer_id or f"{group}-{uuid.uuid4().hex[:8]}"
        self.max_retries = max_retries
        self.base_delay_ms = base_delay_ms
        self.max_delay_ms = max_delay_ms
        self._handlers: dict[str, Handler] = {}
        self._default: Handler | None = None
        self._seen: set[str] = set()
        self._running = False

    def on(self, event_type: str, handler: Handler | None = None):
        """Register the handler for one event type (direct or decorator)."""
        if handler is not None:
            self._handlers[event_type] = handler
            return handler

        def decorator(func: Handler) -> Handler:
            self._handlers[event_type] = func
            return func

        return decorator

    def default(self, handler: Handler) -> Handler:
        """Fallback for event types without a handler."""
        self._default = handler
        return handler

    def _backoff_s(self, attempt: int) -> float:
        delay_ms = min(self.base_delay_ms * (2 ** max(0, attempt - 1)), self.max_delay_ms)
        return delay_ms / 1000.0

    async def _process(self, stream: str, message_id: str, event: Event, stats: RunStats) -> None:
        if event.idempotency_key in self._seen:
            stats.duplicates += 1
            await self.backend.acknowledge(stream, self.group, message_id)
            return
        handler = self._handlers.get(event.type) or self._default
        if handler is None:
            await self.backend.acknowledge(stream, self.group, message_id)
            return
        attempt = 0
        last_error = ""
        handled = False
        while attempt <= self.max_retries:
            attempt += 1
            try:
                await handler(event)
                handled = True
                break
            except Exception as exc:  # noqa: BLE001 - handler failure is data
                last_error = str(exc)[:2000]
                stats.errors += 1
                if attempt <= self.max_retries:
                    await asyncio.sleep(self._backoff_s(attempt))
        if not handled:
            await self.backend.to_dlq(stream, event, f"{last_error} (after {attempt} attempts)")
            stats.dlq += 1
            await self.backend.acknowledge(stream, self.group, message_id)
            return
        # The side effect happened. A failed acknowledgement must not run the
        # handler again in this pass: the delivery stays pending and a broker
        # redelivery (or a replay) resolves it, which is what the idempotency
        # key exists for. `drain_once` counts the failure as a backend error.
        self._seen.add(event.idempotency_key)
        stats.processed += 1
        await self.backend.acknowledge(stream, self.group, message_id)

    async def drain_once(self, *, count: int = 10) -> RunStats:
        """Process everything currently available, then return (tests + jobs).

        A broker that cannot be reached is a counted condition, not a crash:
        the pass reports ``backend_errors``, the worker keeps whatever was
        processed, and `serve()` backs off before the next pass. This is what
        makes a broker restart a recoverable event instead of a lost worker
        (post-RC item [102], chaos suite).
        """
        stats = RunStats()
        for stream in self.streams:
            try:
                await self.backend.ensure_group(stream, self.group)
                while True:
                    batch = await self.backend.read_group(
                        stream, self.group, self.consumer_id, count
                    )
                    if not batch:
                        break
                    for message_id, event in batch:
                        try:
                            await self._process(stream, message_id, event, stats)
                        except Exception:  # noqa: BLE001 - a dead ack is data too
                            stats.backend_errors += 1
            except Exception:  # noqa: BLE001 - broker unavailable this pass
                stats.backend_errors += 1
        return stats

    async def replay(self, stream: str) -> RunStats:
        """Reprocess the full stream history in order (acks untouched)."""
        stats = RunStats()
        for _, event in await self.backend.read_range(stream):
            handler = self._handlers.get(event.type) or self._default
            if handler is None:
                continue
            try:
                await handler(event)
                stats.processed += 1
            except Exception:  # noqa: BLE001 - replay failures are counted, not raised
                stats.errors += 1
        return stats

    async def redrive_dlq(self, stream: str) -> RunStats:
        """Re-handle the parked events of one stream (post-RC item [102]).

        A DLQ entry is kept as evidence, not consumed: redriving runs the
        handler again and republishes idempotent verdicts, so a recovered
        dependency can finish work that was parked during an outage without
        erasing the record of the outage itself.
        """
        stats = RunStats()
        for _message_id, event, _reason in await self.backend.dlq_list(stream):
            handler = self._handlers.get(event.type) or self._default
            if handler is None:
                continue
            try:
                await handler(event)
                stats.processed += 1
            except Exception:  # noqa: BLE001 - still broken: stays parked
                stats.errors += 1
        return stats

    async def serve(self, *, poll_s: float = 0.2, count: int = 10) -> RunStats:
        """Consume until `stop()`; returns totals (long-running workers)."""
        self._running = True
        totals = RunStats()
        try:
            while self._running:
                batch_stats = await self.drain_once(count=count)
                totals.processed += batch_stats.processed
                totals.errors += batch_stats.errors
                totals.dlq += batch_stats.dlq
                totals.duplicates += batch_stats.duplicates
                totals.backend_errors += batch_stats.backend_errors
                if batch_stats.processed == 0:
                    await asyncio.sleep(poll_s * min(8, 1 + batch_stats.backend_errors))
        except asyncio.CancelledError:
            self._running = False
            raise
        return totals

    def stop(self) -> None:
        """Ask `serve()` to exit after the current pass."""
        self._running = False
