"""Phase 11 item [104]: start a persisted workflow version as a real run.

`POST /executions` may not block: a DAG run takes as long as its slowest
step. This module is the bridge between the request and the executor.

- the execution row is inserted **before** the response, so the client can
  poll `GET /executions/{id}` immediately and never sees a dangling id;
- the run itself happens on one background thread with its own asyncio loop,
  using the same in-process bus and worker set the acceptance tests drive
  (`MemoryBackend`, shell/approval/custom workers plus the two GUI stubs),
  so the API is never a second, divergent run path;
- the pinned `dsl_text` from the immutable version row is what executes —
  never a mutable file — and its results are persisted with
  :meth:`ExecutionStore.record_result`;
- failures (an exception, a crashed thread) mark the execution FAILED with
  the reason instead of leaving it RUNNING forever.

The shell `exec_fn` and workspace root are constructor arguments so a test
can pin a deterministic command runner; production leaves them unset and the
real sandbox is used.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from bus.backend import MemoryBackend
from bus.consumer import Runner
from bus.workers.approval import make_approval_worker
from bus.workers.custom import make_custom_worker
from bus.workers.dispatcher import pump_workers, run_workflow
from bus.workers.shell import make_shell_worker
from bus.workers.stubs import make_stub_worker
from engine.executor.result import RunResult
from storage.audit import AuditStore

from .db import init_db
from .models import ExecutionStore, Recorder, WorkflowStore
from .roles import Permission, has_permission

#: Node types the API run path serves. `gui_*` fail closed by design.
STUB_TYPES: tuple[str, ...] = ("gui_click", "gui_type")


class RunError(RuntimeError):
    """A run cannot be started (unknown version, unauthorized role)."""


class RunService:
    """Start one persisted version per call, each on its own background thread."""

    def __init__(
        self,
        db_path: str,
        *,
        exec_fn: Callable[..., Any] | None = None,
        workspace_root: str | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.exec_fn = exec_fn
        self.workspace_root = workspace_root
        self._threads: dict[str, threading.Thread] = {}

    # -- lifecycle --------------------------------------------------------
    def start(
        self,
        workflow_id: str,
        version: int,
        *,
        triggered_by: str = "manual",
        role: str = "OPERATOR",
    ) -> dict:
        """Persist the RUNNING execution row and start its background run."""
        if not has_permission(role, Permission.WORKFLOW_RUN):
            raise PermissionError(f"role {role!r} lacks permission {Permission.WORKFLOW_RUN.value}")
        db = init_db(self.db_path)
        pinned = WorkflowStore(db).get_version(workflow_id, int(version))
        if pinned is None:
            raise KeyError(f"workflow {workflow_id} version {version} is not persisted")
        execution = ExecutionStore(db).start(workflow_id, int(version), triggered_by=triggered_by)
        execution_id = str(execution["id"])
        thread = threading.Thread(
            target=self._thread_main,
            args=(
                str(pinned["dsl_text"]),
                execution_id,
                str(triggered_by),
                str(role),
            ),
            name=f"run-{execution_id[:8]}",
            daemon=True,
        )
        self._threads[execution_id] = thread
        thread.start()
        return execution

    def join(self, execution_id: str, timeout: float | None = None) -> bool:
        """Wait for one background run (tests / orderly shutdown); True if done."""
        thread = self._threads.get(execution_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _thread_main(self, dsl_text: str, execution_id: str, triggered_by: str, role: str) -> None:
        """Run one DAG and persist its outcome; never leave RUNNING behind."""
        try:
            run = asyncio.run(
                self.execute(
                    dsl_text,
                    execution_id,
                    trigger=triggered_by,
                    role=role,
                )
            )
            ExecutionStore(init_db(self.db_path)).record_result(execution_id, run)
        except Exception as exc:  # noqa: BLE001 - the failure is the run's result
            ExecutionStore(init_db(self.db_path)).finish(execution_id, "FAILED", str(exc)[:2000])

    # -- execution --------------------------------------------------------
    async def execute(
        self,
        dsl_text: str,
        run_id: str,
        *,
        trigger: str = "manual",
        role: str = "OPERATOR",
    ) -> RunResult:
        """Execute one pinned DSL document over the in-process bus."""
        db = init_db(self.db_path)
        backend = MemoryBackend()
        group = f"api-{run_id[:12]}"
        workers: list[Runner] = [
            make_shell_worker(backend, group, exec_fn=self.exec_fn),
            make_approval_worker(backend, group, {"recorder": Recorder(db)}),
            make_custom_worker(backend, group),
            *[make_stub_worker(backend, group, node_type) for node_type in STUB_TYPES],
        ]
        stop = asyncio.Event()
        pump = asyncio.create_task(pump_workers(workers, stop))
        try:
            return await run_workflow(
                backend,
                "",
                workflows_dir="",
                run_id=run_id,
                trigger=trigger,
                dsl_text=dsl_text,
                audit_store=AuditStore(db),
                workspace_root=self.workspace_root,
                role=role,
                execution_id=run_id,
            )
        finally:
            stop.set()
            await pump
