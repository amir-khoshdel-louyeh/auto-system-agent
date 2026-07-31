"""Trigger adapters: manual, file.arrived and schedule.tick (item [44], FR-04).

All three trigger types funnel into the shared `run_workflow` path, so a
manual API run, a file-arrival event and a cron tick behave identically once
the plan is created. A manual trigger publishes no synthetic bus event — the
API calls it directly — while `file.arrived` / `schedule.tick` wrap their
frozen contract event around the same run.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from engine.executor.result import RunResult

from .backend import Backend
from .events import Event, new_event
from .workers.dispatcher import dispatch_trigger, run_workflow

MANUAL = "manual"
FILE_ARRIVED = "file.arrived"
SCHEDULE_TICK = "schedule.tick"

#: The three supported trigger kinds (proposal §4.1).
TRIGGER_KINDS: tuple[str, ...] = (MANUAL, FILE_ARRIVED, SCHEDULE_TICK)


def build_trigger_event(
    kind: str,
    workflow: str,
    *,
    correlation_id: str = "",
    idempotency_key: str = "",
) -> Event:
    """Build the frozen contract event for a bus-driven trigger kind."""
    if kind not in (FILE_ARRIVED, SCHEDULE_TICK):
        raise ValueError(f"{kind!r} is not a bus trigger; use run_workflow for manual")
    return new_event(
        kind,
        {"workflow": workflow, "trigger": kind},
        correlation_id=correlation_id,
        idempotency_key=idempotency_key or f"plan:{uuid.uuid4().hex}",
    )


async def trigger_manual(
    backend: Backend,
    workflow: str,
    *,
    workflows_dir: str | Path,
    run_id: str = "",
    step_timeout_s: float = 30.0,
    recorder=None,
) -> RunResult:
    """Run a workflow directly (manual API trigger)."""
    return await run_workflow(
        backend,
        workflow,
        workflows_dir=workflows_dir,
        run_id=run_id,
        trigger=MANUAL,
        step_timeout_s=step_timeout_s,
        recorder=recorder,
    )


async def trigger_file_arrived(
    backend: Backend,
    workflow: str,
    *,
    workflows_dir: str | Path,
    run_id: str = "",
    step_timeout_s: float = 30.0,
    recorder=None,
) -> RunResult:
    """Run a workflow from a `file.arrived` event."""
    event = build_trigger_event(
        FILE_ARRIVED,
        workflow,
        correlation_id=run_id,
        idempotency_key=f"plan:{run_id}" if run_id else "",
    )
    return await dispatch_trigger(
        backend,
        event,
        workflows_dir=workflows_dir,
        run_id=run_id,
        step_timeout_s=step_timeout_s,
        recorder=recorder,
    )


async def trigger_schedule_tick(
    backend: Backend,
    workflow: str,
    *,
    workflows_dir: str | Path,
    run_id: str = "",
    step_timeout_s: float = 30.0,
    recorder=None,
) -> RunResult:
    """Run a workflow from a `schedule.tick` (cron) event."""
    event = build_trigger_event(
        SCHEDULE_TICK,
        workflow,
        correlation_id=run_id,
        idempotency_key=f"cron:{run_id}" if run_id else "",
    )
    return await dispatch_trigger(
        backend,
        event,
        workflows_dir=workflows_dir,
        run_id=run_id,
        step_timeout_s=step_timeout_s,
        recorder=recorder,
    )
