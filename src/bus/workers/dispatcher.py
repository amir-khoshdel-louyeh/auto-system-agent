"""Trigger dispatcher: file.arrived/schedule.tick → validated run over the bus.

Own orchestration (no copy): loads the named YAML workflow, validates it
(invalid DSL raises before anything is published — never partial deploy),
publishes the frozen `plan.created` contract, then executes the compiled DAG
through the phase-2 scheduler with a bus-aware handler: every node becomes
a `step.dispatched` task on its `steps:<type>` stream and the dispatcher
waits for the worker verdict on `steps:results`. Runs and stub approvals
are recorded via `app.models.Recorder` when one is provided.

`run_workflow` is the single run path shared by every trigger (item [44]):
`dispatch_trigger` wraps a `file.arrived` / `schedule.tick` event around it,
while a manual trigger (the API) calls it directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

from engine.dsl.compiler import compile as compile_wf
from engine.dsl.loader import load_text
from engine.executor.gate import decide
from engine.executor.result import RunResult
from engine.executor.scheduler import run_dag
from engine.validator.validator import validate

from ..backend import Backend
from ..consumer import Runner
from ..events import Event, new_event
from .core import RESULTS_STREAM, steps_stream

TRIGGERS_STREAM = "triggers"
PLANS_STREAM = "plans"

TRIGGER_TYPES = ("file.arrived", "schedule.tick")


async def wait_verdict(
    backend: Backend, run_id: str, node_id: str, *, timeout_s: float = 30.0
) -> dict[str, Any]:
    """Poll `steps:results` for one node's verdict (finished → output)."""
    seen: set[str] = set()
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        for message_id, event in await backend.read_range(RESULTS_STREAM):
            if message_id in seen:
                continue
            seen.add(message_id)
            if event.correlation_id != run_id or event.data.get("node_id") != node_id:
                continue
            if event.type == "step.finished":
                output = event.data.get("output", {})
                return output if isinstance(output, dict) else {"output": output}
            if event.type == "step.failed":
                raise RuntimeError(event.data.get("error", "step failed"))
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"no verdict for node '{node_id}' in {timeout_s:g}s")
        await asyncio.sleep(0.05)


async def pump_workers(workers: list[Runner], stop: asyncio.Event) -> None:
    """Drain every worker until `stop` is set (tests + local runs)."""
    while not stop.is_set():
        moved = False
        for worker in workers:
            stats = await worker.drain_once()
            moved = moved or stats.processed > 0 or stats.errors > 0
        if not moved:
            await asyncio.sleep(0.02)


async def run_workflow(
    backend: Backend,
    workflow_name: str,
    *,
    workflows_dir: str | Path,
    run_id: str = "",
    trigger: str = "manual",
    step_timeout_s: float = 30.0,
    recorder=None,
) -> RunResult:
    """Load, validate and execute one workflow over the bus; returns the run."""
    path = Path(workflows_dir) / f"{workflow_name}.yaml"
    workflow = load_text(path.read_text(encoding="utf-8"))
    errors = validate(workflow)
    if errors:
        raise ValueError(f"invalid workflow '{workflow_name}': {errors[0]}")
    dag = compile_wf(workflow)
    run_id = run_id or uuid.uuid4().hex

    await backend.publish(
        PLANS_STREAM,
        new_event(
            "plan.created",
            {
                "run_id": run_id,
                "workflow": workflow.name,
                "version": workflow.version,
                "trigger": trigger,
            },
            correlation_id=run_id,
            idempotency_key=f"plan:{run_id}",
        ),
    )

    execution_id = ""
    if recorder is not None:
        wid = recorder.ensure_workflow(workflow.name)
        dsl_hash = hashlib.sha256(workflow.raw_text.encode("utf-8")).hexdigest()
        recorder.add_version(wid, workflow.version, workflow.raw_text, dsl_hash)
        execution_id = recorder.start_execution(wid, workflow.version, triggered_by=trigger).id

    async def bus_handler(node_id: str, node: dict[str, Any]) -> dict[str, Any]:
        await backend.publish(
            steps_stream(node.get("type", "")),
            new_event(
                "step.dispatched",
                {"node_id": node_id, "node": node, "execution_id": execution_id},
                correlation_id=run_id,
                idempotency_key=f"{run_id}:{node_id}",
            ),
        )
        return await wait_verdict(backend, run_id, node_id, timeout_s=step_timeout_s)

    run = await run_dag(dag, bus_handler, gate=decide, run_id=run_id)
    for nid, node in run.nodes.items():
        if node.status == "FAILED":
            await backend.publish(
                RESULTS_STREAM,
                new_event(
                    "step.failed",
                    {"node_id": nid, "error": node.error},
                    correlation_id=run_id,
                    idempotency_key=f"{run_id}:{nid}:failed",
                ),
            )
    if recorder is not None and execution_id:
        recorder.finish_execution(execution_id, run.status, run.error)
    return run


async def dispatch_trigger(
    backend: Backend,
    event: Event,
    *,
    workflows_dir: str | Path,
    run_id: str = "",
    step_timeout_s: float = 30.0,
    recorder=None,
) -> RunResult:
    """Run one trigger event end-to-end; returns the scheduler RunResult."""
    if event.type not in TRIGGER_TYPES:
        raise ValueError(f"dispatcher handles {TRIGGER_TYPES}, got {event.type!r}")
    return await run_workflow(
        backend,
        event.data.get("workflow", "onboarding-demo"),
        workflows_dir=workflows_dir,
        run_id=run_id,
        trigger=event.type,
        step_timeout_s=step_timeout_s,
        recorder=recorder,
    )
