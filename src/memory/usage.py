"""Phase 7 item [72]: reuse-hit and no-generation-cost metrics (FR-16).

Every planning request first consults memory. This module records that
consultation and turns the rows into the two numbers the proposal asks for:

- **reuse hit** — the request was answered with an existing ``PUBLIC`` plan,
  so the workflow could be built from memory;
- **no-generation cost** — a hit means the planner's generation call never
  happened, so the share of requests served with zero generation is the
  saving the memory layer buys.

The query itself is never stored: only its SHA-256 digest and length, so the
metrics table cannot become a second, unscrubbed copy of user input.

All aggregation is a pure function over rows, mirroring
``storage.run_metrics`` — the numbers are reproducible and unit-testable
without a live database.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .ids import new_id, utcnow

__all__ = [
    "ReuseMetrics",
    "list_retrievals",
    "query_hash",
    "record_retrieval",
    "summarize",
]


def query_hash(query: str) -> str:
    """Stable digest of a retrieval query (raw text is never persisted)."""
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def record_retrieval(
    path: str | Path,
    *,
    query: str,
    hit: bool,
    score: float = 0.0,
    hit_id: str = "",
    now: str = "",
) -> None:
    """Append one memory consultation (hit = answered without generation)."""
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO memory_retrievals"
            " (id, query_hash, query_length, hit, hit_id, score, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                query_hash(query),
                len(query or ""),
                1 if hit else 0,
                str(hit_id),
                round(float(score), 6),
                now or utcnow(),
            ),
        )


def list_retrievals(path: str | Path, *, limit: int = 1000) -> list[dict]:
    """Most recent consultations first, capped by ``limit``."""
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM memory_retrievals ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(row) for row in rows]


@dataclass(frozen=True)
class ReuseMetrics:
    """Reuse-hit and no-generation accounting (item [72])."""

    requests: int
    reuse_hits: int
    memory_misses: int
    hit_rate: float
    no_generation_rate: float
    planner_generations: int
    planner_calls_avoided: int
    avg_hit_score: float

    def as_dict(self) -> dict:
        return {
            "requests": self.requests,
            "reuse_hits": self.reuse_hits,
            "memory_misses": self.memory_misses,
            "hit_rate": self.hit_rate,
            "no_generation_rate": self.no_generation_rate,
            "planner_generations": self.planner_generations,
            "planner_calls_avoided": self.planner_calls_avoided,
            "avg_hit_score": self.avg_hit_score,
        }


def summarize(retrievals: list[dict]) -> ReuseMetrics:
    """Aggregate consultation rows into reuse-hit and generation metrics.

    A miss is a request memory could not answer, so it costs exactly one
    planner generation; a hit costs none. Both rates are 0..1 fractions so
    they compose with the rest of the metrics layer.
    """
    requests = len(retrievals)
    hits = [row for row in retrievals if int(row.get("hit", 0))]
    reuse_hits = len(hits)
    misses = requests - reuse_hits
    scores = [float(row.get("score", 0.0)) for row in hits]
    return ReuseMetrics(
        requests=requests,
        reuse_hits=reuse_hits,
        memory_misses=misses,
        hit_rate=round(reuse_hits / requests, 4) if requests else 0.0,
        no_generation_rate=round(reuse_hits / requests, 4) if requests else 0.0,
        planner_generations=misses,
        planner_calls_avoided=reuse_hits,
        avg_hit_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
    )
