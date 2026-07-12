"""Executor result types: plain data, no execution intelligence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Per-node terminal states.
OK = "OK"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
CANCELLED = "CANCELLED"

#: Whole-run terminal states.
COMPLETED = "COMPLETED"
RUN_FAILED = "FAILED"
RUN_CANCELLED = "CANCELLED"


@dataclass
class NodeResult:
    """Outcome of one DAG node."""

    node_id: str
    status: str = FAILED
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    attempts: int = 0
    duration_ms: float = 0.0


@dataclass
class RunResult:
    """Outcome of one DAG run (nodes keyed by id, execution order kept)."""

    run_id: str
    status: str = RUN_FAILED
    nodes: dict[str, NodeResult] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    error: str = ""
