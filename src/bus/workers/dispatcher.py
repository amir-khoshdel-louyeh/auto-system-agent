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
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from engine.dsl.compiler import compile as compile_wf
from engine.dsl.loader import load_text
from engine.executor.gate import flatten_node
from engine.executor.result import RunResult
from engine.executor.scheduler import run_dag
from engine.validator.validator import validate
from safety.policy_service import PolicyContext, PolicyService
from storage.audit import AuditStore, AuditTrail

from ..backend import Backend
from ..consumer import Runner
from ..events import Event, new_event
from .core import RESULTS_STREAM, steps_stream

TRIGGERS_STREAM = "triggers"
PLANS_STREAM = "plans"

TRIGGER_TYPES = ("file.arrived", "schedule.tick")

#: Environment knob for the directory that may hold per-run workspaces.
WORKSPACE_ROOT_ENV = "ASA_WORKSPACE_ROOT"


def resolve_run_workspace(
    run_id: str, workspace_root: str | Path | None = None
) -> tuple[Path, bool]:
    """Return ``(workspace, temporary)`` for one run (item [99]).

    A configured root (argument first, then ``ASA_WORKSPACE_ROOT``) gives the
    run a durable directory, so shell commands leave their evidence where an
    operator can look at it. With no root configured the run gets a throwaway
    directory under the system temp, which the caller removes — a library run
    must never litter the repository.
    """
    root = Path(workspace_root) if workspace_root is not None else None
    if root is None:
        configured = os.environ.get(WORKSPACE_ROOT_ENV, "").strip()
        root = Path(configured) if configured else None
    if root is None:
        return Path(tempfile.mkdtemp(prefix=f"run-{run_id[:12]}-")), True
    workspace = root / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace, False


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
    dsl_text: str | None = None,
    audit_store: AuditStore | None = None,
    workspace_root: str | Path | None = None,
    role: str = "OPERATOR",
    execution_id: str = "",
) -> RunResult:
    """Load, validate and execute one workflow over the bus; returns the run.

    ``dsl_text`` executes a *pinned* document (replay, item [52]) instead of
    reading ``<workflow_name>.yaml`` from disk, so a replay never picks up a
    newer mutable file.

    ``workspace_root`` (item [99]) decides where the run's workspace lives;
    it is created here, handed to the workers with every node, and used as the
    policy gate's path boundary, so a command that reaches outside it is
    denied *before* it is dispatched. ``role`` is the effective caller role
    the gate authorizes.

    With an ``audit_store`` the run writes its own hash-chain records
    (run, policy, dispatch, retry, result) keyed by the execution id, so
    every execution has an auditable trail (item [48], FR-12).

    ``execution_id`` pins the id the audit chain and step events use. An API
    caller (item [104]) persists the RUNNING execution row before starting
    the run, so it passes the id here with ``recorder=None`` and records the
    node results itself; without it the behaviour is unchanged.
    """
    if dsl_text is None:
        path = Path(workflows_dir) / f"{workflow_name}.yaml"
        dsl_text = path.read_text(encoding="utf-8")
    workflow = load_text(dsl_text)
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

    execution_id = str(execution_id or "")
    if recorder is not None and not execution_id:
        wid = recorder.ensure_workflow(workflow.name)
        dsl_hash = hashlib.sha256(workflow.raw_text.encode("utf-8")).hexdigest()
        recorder.add_version(wid, workflow.version, workflow.raw_text, dsl_hash)
        execution_id = recorder.start_execution(wid, workflow.version, triggered_by=trigger).id

    trail = AuditTrail(audit_store, execution_id or run_id) if audit_store is not None else None
    if trail is not None:
        trail.run_started(workflow.name, workflow.version, trigger=trigger, run_id=run_id)

    def audit_event(name: str, payload: dict[str, Any]) -> None:
        """Map scheduler events onto the FR-12 audit vocabulary."""
        if trail is None or name in ("run.started", "run.finished"):
            return
        node_id = str(payload.get("node_id", ""))
        if name == "node.started":
            trail.dispatch(node_id, run_id=run_id)
        elif name == "node.retry":
            trail.retry(
                node_id,
                int(payload.get("attempt", 1)),
                str(payload.get("error", "")),
                run_id=run_id,
            )
        elif name == "node.finished":
            trail.result(node_id, str(payload.get("status", "")), run_id=run_id)
        else:
            trail.record(name, payload)

    policy = PolicyService()
    workspace, throwaway_workspace = resolve_run_workspace(run_id, workspace_root)
    context = PolicyContext(role=role, workspace_root=str(workspace))

    async def audited_gate(node_id: str, node: dict[str, Any]) -> str:
        """Policy gate that also lands the decision on the audit chain."""
        decision = policy.decide(node_id, flatten_node(node), context=context)
        if trail is not None:
            trail.policy(node_id, decision)
        return decision.verdict

    async def bus_handler(node_id: str, node: dict[str, Any]) -> dict[str, Any]:
        await backend.publish(
            steps_stream(node.get("type", "")),
            new_event(
                "step.dispatched",
                {
                    "node_id": node_id,
                    "node": node,
                    "execution_id": execution_id,
                    "workspace": str(workspace),
                },
                correlation_id=run_id,
                idempotency_key=f"{run_id}:{node_id}",
            ),
        )
        return await wait_verdict(backend, run_id, node_id, timeout_s=step_timeout_s)

    try:
        run = await run_dag(
            dag,
            bus_handler,
            gate=audited_gate,
            on_event=audit_event if trail is not None else None,
            run_id=run_id,
        )
    finally:
        if throwaway_workspace:
            shutil.rmtree(workspace, ignore_errors=True)
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
    if trail is not None:
        trail.run_finished(run.status, run.error)
    return run


async def dispatch_trigger(
    backend: Backend,
    event: Event,
    *,
    workflows_dir: str | Path,
    run_id: str = "",
    step_timeout_s: float = 30.0,
    recorder=None,
    audit_store: AuditStore | None = None,
    workspace_root: str | Path | None = None,
    role: str = "OPERATOR",
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
        audit_store=audit_store,
        workspace_root=workspace_root,
        role=role,
    )
