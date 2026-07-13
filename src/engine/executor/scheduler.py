"""Phase 2 scheduler: topo levels + parallel asyncio run (adapted, src-only).

Mirrors the shape of source_MIT/optiflow
`backend/app/services/execution_service.py::_run_dag` (ready-set grouping,
`asyncio.gather` parallelism, per-node try/except) with deliberate deltas:

- no DB session: input is the compiled DAG dict from `dsl.compiler.compile`;
- no unsafe `exec()`: node work arrives via an injected async `handler`
  (custom logic only via the Plugin SDK, phase 7);
- no profiler / AI-suggest (deferred per plan);
- levels are computed up front (Kahn), so the dynamic deadlock loop of
  upstream collapses to a single upfront cycle check — the validator must
  have accepted the workflow first; cycles/unknown refs raise ValueError
  and nothing runs (never partial deploy).

A `gate` hook (`ALLOW` / `NEED_APPROVAL` / `DENY`) runs before every node.
`DENY` fails the node and skips its downstream; `NEED_APPROVAL` skips the
node until an approver is wired (phase 6 approval worker).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .result import (
    CANCELLED,
    COMPLETED,
    FAILED,
    OK,
    RUN_CANCELLED,
    RUN_FAILED,
    SKIPPED,
    NodeResult,
    RunCancelled,
    RunResult,
)

#: (node_id, node_spec) -> output dict. Raises on node failure.
Handler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

#: (node_id, node_spec) -> "ALLOW" | "NEED_APPROVAL" | "DENY".
Gate = Callable[[str, dict[str, Any]], Awaitable[str]]

#: (event_name, payload) -> None. Audit store subscribes here (later commit).
OnEvent = Callable[[str, dict[str, Any]], None]


def compute_levels(
    nodes: dict[str, Any], edges: list[tuple[str, str]]
) -> list[list[str]]:
    """Group node ids into Kahn levels; raises ValueError on bad refs/cycles."""
    children: dict[str, list[str]] = {nid: [] for nid in nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in nodes}
    for src, dst in edges:
        if src not in nodes or dst not in nodes:
            raise ValueError(f"unknown node ref in edge [{src!r}, {dst!r}]")
        children[src].append(dst)
        in_degree[dst] += 1
    levels: list[list[str]] = []
    ready = sorted(nid for nid, deg in in_degree.items() if deg == 0)
    while ready:
        levels.append(ready)
        upcoming: list[str] = []
        for nid in ready:
            for child in children[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    upcoming.append(child)
        ready = sorted(upcoming)
    if sum(len(level) for level in levels) != len(nodes):
        raise ValueError("workflow contains a cycle; cannot execute")
    return levels


async def run_dag(
    dag: dict[str, Any],
    handler: Handler,
    *,
    timeout_s: float = 60.0,
    max_retries: int = 0,
    gate: Gate | None = None,
    on_event: OnEvent | None = None,
    run_id: str | None = None,
) -> RunResult:
    """Execute one compiled DAG level by level, nodes inside a level in parallel."""
    nodes: dict[str, Any] = dag.get("nodes", {})
    edges: list[tuple[str, str]] = dag.get("edges", [])
    levels = compute_levels(nodes, edges)
    parents: dict[str, list[str]] = {nid: [] for nid in nodes}
    for src, dst in edges:
        parents[dst].append(src)

    run = RunResult(run_id=run_id or uuid.uuid4().hex)

    def emit(name: str, payload: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(name, {"run_id": run.run_id, **payload})

    async def run_one(nid: str) -> None:
        spec = nodes[nid]
        dep_states = {dep: run.nodes[dep].status for dep in parents[nid]}
        if any(status != OK for status in dep_states.values()):
            bad = sorted(dep for dep, status in dep_states.items() if status != OK)
            run.nodes[nid] = NodeResult(
                node_id=nid, status=SKIPPED, error=f"skipped: dep(s) not OK: {bad}"
            )
            emit("node.skipped", {"node_id": nid, "deps": dep_states})
            return
        if gate is not None:
            verdict = await gate(nid, spec)
            if verdict == "DENY":
                run.nodes[nid] = NodeResult(node_id=nid, status=FAILED, error="policy DENY")
                emit("node.denied", {"node_id": nid})
                return
            if verdict != "ALLOW":
                run.nodes[nid] = NodeResult(
                    node_id=nid, status=SKIPPED, error="needs-approval: no approver wired (phase 6)"
                )
                emit("node.awaiting_approval", {"node_id": nid})
                return
        emit("node.started", {"node_id": nid})
        started = time.monotonic()
        attempts = 0
        last_error = ""
        while attempts <= max_retries:
            attempts += 1
            try:
                output = await asyncio.wait_for(handler(nid, spec), timeout_s)
                run.nodes[nid] = NodeResult(
                    node_id=nid,
                    status=OK,
                    output=output if isinstance(output, dict) else {"output": output},
                    attempts=attempts,
                    duration_ms=(time.monotonic() - started) * 1000.0,
                )
                emit("node.finished", {"node_id": nid, "status": OK})
                return
            except asyncio.CancelledError:
                run.nodes[nid] = NodeResult(node_id=nid, status=CANCELLED, error="run cancelled")
                emit("node.finished", {"node_id": nid, "status": CANCELLED})
                raise
            except asyncio.TimeoutError:
                last_error = f"timeout after {timeout_s:g}s (attempt {attempts})"
            except Exception as exc:  # noqa: BLE001 - node failure is data, not a crash
                last_error = str(exc)[:2000]
            emit("node.retry", {"node_id": nid, "attempt": attempts, "error": last_error})
        run.nodes[nid] = NodeResult(
            node_id=nid,
            status=FAILED,
            error=last_error,
            attempts=attempts,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )
        emit("node.finished", {"node_id": nid, "status": FAILED})

    emit("run.started", {"nodes": sorted(nodes)})
    try:
        for level in levels:
            await asyncio.gather(*(run_one(nid) for nid in level))
            run.order.extend(level)
    except asyncio.CancelledError:
        run.status = RUN_CANCELLED
        run.error = "run cancelled"
        emit("run.finished", {"status": RUN_CANCELLED})
        raise RunCancelled(run) from None

    if all(run.nodes[nid].status == OK for nid in nodes):
        run.status = COMPLETED
    else:
        run.status = RUN_FAILED
        bad = sorted(nid for nid in nodes if run.nodes[nid].status != OK)
        run.error = f"{len(bad)} node(s) not OK: {bad}"
    emit("run.finished", {"status": run.status})
    return run
