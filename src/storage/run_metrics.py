"""Phase 5 item [51]: execution metrics (success, latency, retries, approvals).

Pure functions over persisted rows — no live system needed — so the numbers
the observability layer reports are unit-testable and reproducible. Latency
comes from execution timestamps, retries from step attempts, and approval
time from request creation to the first decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None when unusable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def latency_ms(execution: dict) -> float | None:
    """Wall-clock duration of one execution in milliseconds, or None."""
    start = parse_ts(str(execution.get("started_at", "")))
    end = parse_ts(str(execution.get("finished_at", "")))
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds() * 1000.0)


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100); 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round((p / 100.0) * (len(ordered) - 1))))
    return round(ordered[rank], 2)


@dataclass(frozen=True)
class RunMetrics:
    """Aggregate execution metrics (FR-16)."""

    total: int
    succeeded: int
    failed: int
    success_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    retries: int
    approval_count: int
    avg_approval_s: float

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "success_rate": self.success_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "retries": self.retries,
            "approval_count": self.approval_count,
            "avg_approval_s": self.avg_approval_s,
        }


def summarize(
    executions: list[dict],
    *,
    steps: list[dict] | None = None,
    requests: list[dict] | None = None,
    decisions: list[dict] | None = None,
) -> RunMetrics:
    """Aggregate execution + step + approval rows into one metrics record."""
    total = len(executions)
    succeeded = sum(1 for e in executions if e.get("status") == "COMPLETED")
    failed = sum(1 for e in executions if e.get("status") == "FAILED")
    completed = [latency_ms(e) for e in executions]
    latencies = [value for value in completed if value is not None]

    retries = sum(1 for step in (steps or []) if int(step.get("attempt", 1)) > 1)

    decision_by_node: dict[tuple[str, str], str] = {}
    for decision in decisions or []:
        key = (str(decision.get("execution_id", "")), str(decision.get("node_id", "")))
        decided_at = str(decision.get("decided_at", ""))
        if decided_at and (key not in decision_by_node or decided_at < decision_by_node[key]):
            decision_by_node[key] = decided_at
    approval_durations: list[float] = []
    for request in requests or []:
        created = parse_ts(str(request.get("created_at", "")))
        first_decision = decision_by_node.get(
            (str(request.get("execution_id", "")), str(request.get("node_id", "")))
        )
        decided = parse_ts(first_decision or "")
        if created is not None and decided is not None:
            approval_durations.append(max(0.0, (decided - created).total_seconds()))

    return RunMetrics(
        total=total,
        succeeded=succeeded,
        failed=failed,
        success_rate=round(100.0 * succeeded / total, 2) if total else 100.0,
        avg_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        p95_latency_ms=percentile(latencies, 95),
        retries=retries,
        approval_count=len(approval_durations),
        avg_approval_s=round(sum(approval_durations) / len(approval_durations), 2)
        if approval_durations
        else 0.0,
    )
