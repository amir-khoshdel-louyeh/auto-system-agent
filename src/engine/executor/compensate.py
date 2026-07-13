"""Saga-style compensation: undo completed nodes in reverse order.

After a kill/failure mid-run (e.g. kill during install), the caller runs
`compensate()` over the finished `RunResult`; every node that reached OK is
undone newest-first. Compensation itself never raises — failures are
reported per node so the audit trail stays complete.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .result import OK, RunResult

#: (node_id, node_output) -> None. Raises on compensation failure.
Compensator = Callable[[str, dict[str, Any]], Awaitable[None]]


async def compensate(
    run: RunResult,
    compensator: Compensator,
    *,
    timeout_s: float = 60.0,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[tuple[str, str]]:
    """Compensate OK nodes newest-first; returns [(node_id, outcome)]."""
    outcomes: list[tuple[str, str]] = []
    for nid in reversed(run.order):
        node = run.nodes.get(nid)
        if node is None or node.status != OK:
            continue
        try:
            await asyncio.wait_for(compensator(nid, node.output), timeout_s)
            outcomes.append((nid, "OK"))
            if on_event is not None:
                on_event("node.compensated", {"run_id": run.run_id, "node_id": nid})
        except Exception as exc:  # noqa: BLE001 - compensation failure is data
            outcomes.append((nid, f"FAILED: {exc}"[:500]))
            if on_event is not None:
                on_event(
                    "node.compensation_failed",
                    {"run_id": run.run_id, "node_id": nid, "error": str(exc)[:500]},
                )
    return outcomes
