"""Phase 3 event bus (B8): broker + consumer groups + workers (adapted, src-only).

Adapts only source_MIT/EventFlow `core/broker.py` (broker abstraction over
Redis Streams) and `consumers/` (consumer-group loop, retry, DLQ). No
structlog/pydantic/SQLAlchemy; replay and idempotency-keys are our own
additions (upstream has neither). Priority queues, metrics and the HTTP API
are deferred per plan.
"""

__all__ = ["events", "backend", "consumer", "workers"]
