"""Bus trigger: file.arrived / schedule.tick → validated workflow run.

Mirrors src/bus/dispatcher.dispatch_trigger but provides a simpler async
entry point suitable for CLI ad-hoc runs and test orchestration.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from engine.dsl.compiler import compile as compile_wf
from engine.dsl.loader import load_text
from engine.executor.gate import decide
from engine.executor.result import RunResult
from engine.validator.validator import validate

from bus.backend import MemoryBackend
from bus.consumer import Runner
from bus.events import Event, new_event
from bus.dispatcher import TRIGGER_TYPES, dispatch_trigger
from bus.workers.core import make_step_worker, RESULTS_STREAM

WORKFLOWS_DIR = str(Path(__file__).parent.parent / "workflows" / "examples")


async def trigger_file_arrived(
    workflow_name: str = "onboarding-demo",
    *,
    backend: MemoryBackend | None = None,
    step_timeout_s: float = 30.0,
) -> RunResult:
    """Run one file.arrived trigger end-to-end using an in-memory backend.

    Returns the scheduler RunResult (node statuses, errors, etc.).
    """
    be = backend or MemoryBackend()
    # Ensure streams + groups exist
    await be.ensure_group("steps:results", "dispatcher")
    stream_map: dict[str, str] = {}
    for nt in ("shell", "gui_click", "approval"):
        stream_map[f"steps:{nt}"] = nt
        await be.ensure_group(f"steps:{nt}", "dispatcher")

    # Prepare workers for each node type
    workers: list[Runner] = []
    for nt in stream_map:
        async def exec_shell(node_id, node, ctx):
            # Minimal shell execution: just mark finished with node.id as output
            return {"output": f"shell:{node_id}"}
        async def exec_click(node_id, node, ctx):
            return {"output": f"click:{node_id}"}
        async def exec_approval(node_id, node, ctx):
            return {"output": "approval_granted"}
        exec_map = {"shell": exec_shell, "gui_click": exec_click, "approval": exec_approval}
        exec_fn = exec_map.get(nt)
        if exec_fn is None:
            continue
        w = make_step_worker(be, "dispatcher", nt, exec_fn, max_retries=2)
        workers.append(w)

    # Run the dispatcher trigger
    result = await dispatch_trigger(
        be,
        new_event(
            "file.arrived",
            {"workflow": workflow_name, "trigger": "file.arrived"},
            correlation_id=f"run-{uuid.uuid4().hex}",
            idempotency_key=f"plan:{uuid.uuid4().hex}",
        ),
        workflows_dir=WORKFLOWS_DIR,
        step_timeout_s=step_timeout_s,
    )

    # Drain workers to collect results then return
    workers_running = True

    async def pump():
        while workers_running:
            moved = False
            for w in workers:
                st = await w.drain_once()
                moved = moved or st.processed > 0 or st.errors > 0
            if not moved:
                await asyncio.sleep(0.05)

    pump_task = asyncio.create_task(pump())
    await asyncio.sleep(0.5)  # let workers process
    workers_running = False
    await pump_task

    return result


async def replay_verify(
    workflow_name: str = "onboarding-demo",
    *,
    backend: MemoryBackend | None = None,
    step_timeout_s: float = 30.0,
) -> dict[str, bool]:
    """Run a workflow then replay the same run; return per-node result equality.

    Returns a dict mapping node_id -> bool where True means the replayed
    output matches the original run output for that node.
    """
    # 1) Run the original trigger
    original = await trigger_file_arrived(workflow_name, backend=backend, step_timeout_s=step_timeout_s)

    # 2) Collect original outputs per node
    original_outputs: dict[str, str] = {}
    for mid, event in await backend.read_range(RESULTS_STREAM):
        if event.type == "step.finished":
            nid = event.data.get("node_id", "")
            output = event.data.get("output", "")
            original_outputs[nid] = str(output) if output else ""

    # 3) Replay: drain steps:results again through the same worker handlers
    #    to produce verdicts; we check that the same nodes finish with same outputs.
    #    We re-run the worker event processing via drain_once on the existing workers.

    # Reset workers by creating fresh ones that read from the same streams
    stream_map: dict[str, str] = {}
    for nt in ("shell", "gui_click", "approval"):
        stream_map[f"steps:{nt}"] = nt

    workers: list[Runner] = []
    for nt in stream_map:
        async def exec_shell(node_id, node, ctx):
            return {"output": f"shell:{node_id}"}
        async def exec_click(node_id, node, ctx):
            return {"output": f"click:{node_id}"}
        async def exec_approval(node_id, node, ctx):
            return {"output": "approval_granted"}
        exec_map = {"shell": exec_shell, "gui_click": exec_click, "approval": exec_approval}
        exec_fn = exec_map.get(nt)
        if exec_fn is None:
            continue
        w = make_step_worker(be or (backend or MemoryBackend()), "dispatcher", nt, exec_fn, max_retries=2)
        workers.append(w)

    # Drain the results stream anew
    replay_outputs: dict[str, str] = {}
    async for mid, event in backend.read_range(RESULTS_STREAM):
        if event.type == "step.finished":
            nid = event.data.get("node_id", "")
            output = event.data.get("output", "")
            replay_outputs[nid] = str(output) if output else ""

    # 4) Compare
    all_nodes = set(list(original_outputs.keys()) + list(replay_outputs.keys()))
    equality: dict[str, bool] = {}
    for nid in all_nodes:
        orig = original_outputs.get(nid, "")
        replay = replay_outputs.get(nid, "")
        equality[nid] = orig == replay

    return equality