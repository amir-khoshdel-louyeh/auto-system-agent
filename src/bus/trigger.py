"""Bus trigger: file.arrived / schedule.tick → validated workflow run.

Mirrors src/bus/dispatcher.dispatch_trigger but provides a simpler sync+async
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
    from bus.backend import Backend  # noqa: F811

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
    stop = asyncio.Event()
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