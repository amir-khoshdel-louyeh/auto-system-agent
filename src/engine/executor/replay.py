"""Phase 5 item [53]: normalized output comparison for replay (FR-13).

Replay answers one question: did the pinned rerun produce the same
*normalized* outputs as the original run? Raw comparison would report false
mismatches for volatile fields (timings, clocks) and for output ordering, so
both sides go through the same normalisation before they are compared:

- mapping keys are sorted (canonical JSON);
- volatile timing keys are dropped at any depth;
- the serialised form is bounded to the same 500 characters the execution
  store persists, so a truncated original summary still compares equal to
  its rerun.

The comparison is total: missing and unexpected nodes are reported by name,
not folded into a generic ``match: false``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Output keys that describe *when* a step ran, not *what* it did.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "duration",
        "duration_ms",
        "elapsed",
        "elapsed_ms",
        "finished_at",
        "started_at",
        "time",
        "timestamp",
        "ts",
    }
)

#: Same bound as `app.models.summarize_output` (raw output is never unbounded).
MAX_SUMMARY = 500


def _strip_volatile(value: Any) -> Any:
    """Recursively drop volatile timing keys from dicts/lists."""
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if str(key).lower() not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def normalize_output(output: Any, limit: int = MAX_SUMMARY) -> str:
    """Canonical, bounded serialisation of one node output."""
    if output is None:
        output = {}
    try:
        text = json.dumps(_strip_volatile(output), sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        text = str(output)
    return text[:limit]


def normalize_summary(summary: str, limit: int = MAX_SUMMARY) -> str:
    """Normalise a persisted summary string the way a live output is normalised.

    A summary that parses as JSON is re-serialised canonically (so key order
    and volatile keys cannot cause a false mismatch); anything else is
    treated as opaque text and only trimmed.
    """
    text = "" if summary is None else str(summary).strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text[:limit]
    return normalize_output(parsed, limit=limit)


@dataclass(frozen=True)
class ReplayComparison:
    """Result of comparing source and replay outputs node by node (FR-13)."""

    match: bool
    compared: int
    mismatched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    details: dict[str, dict[str, str]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "match": self.match,
            "compared": self.compared,
            "mismatched": list(self.mismatched),
            "missing": list(self.missing),
            "unexpected": list(self.unexpected),
            "details": dict(self.details),
        }


def compare_outputs(
    source: dict[str, str], replay: dict[str, str], *, limit: int = MAX_SUMMARY
) -> ReplayComparison:
    """Compare two ``node_id -> output summary`` mappings after normalisation."""
    mismatched: list[str] = []
    missing: list[str] = []
    unexpected: list[str] = []
    details: dict[str, dict[str, str]] = {}
    compared = 0
    for node_id in sorted(set(source) | set(replay)):
        if node_id not in replay:
            missing.append(node_id)
            continue
        if node_id not in source:
            unexpected.append(node_id)
            continue
        original = normalize_summary(source[node_id], limit=limit)
        rerun = normalize_summary(replay[node_id], limit=limit)
        if original == rerun:
            compared += 1
            continue
        mismatched.append(node_id)
        details[node_id] = {"source": original, "replay": rerun}
    return ReplayComparison(
        match=not mismatched and not missing and not unexpected,
        compared=compared,
        mismatched=mismatched,
        missing=missing,
        unexpected=unexpected,
        details=details,
    )
