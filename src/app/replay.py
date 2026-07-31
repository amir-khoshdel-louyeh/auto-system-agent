"""Phase 5 item [52]: replay a run from its pinned workflow version (FR-13).

Replay never re-reads the mutable workflow file: it loads the *immutable*
``workflow_versions`` row referenced by the source execution, verifies the
stored content hash still matches that row (a modified pinned version is a
replay error, not a silent re-run), and hands exactly that DSL text to the
caller's runner.

The rerun gets a brand-new correlation id (proposal §10.5) while the source
execution id stays in the result and in the ``run.replay`` audit record, so
the two runs are linked but never confused. Comparison of normalized outputs
lives in :mod:`engine.executor.replay`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from engine.executor.replay import ReplayComparison, compare_outputs
from engine.executor.result import RunResult
from storage.audit import AuditStore, AuditTrail

from .models import ExecutionStore, WorkflowStore, dsl_hash
from .roles import Permission, has_permission

#: (pinned DSL text, replay correlation id) -> completed run.
Runner = Callable[[str, str], Awaitable[RunResult]]


class ReplayError(RuntimeError):
    """A run cannot be replayed faithfully (unknown version, tampered pin)."""


@dataclass(frozen=True)
class ReplayResult:
    """One replay: new execution, pinned source version, comparison verdict."""

    source_execution_id: str
    replay_execution_id: str
    workflow_id: str
    version: int
    status: str
    comparison: ReplayComparison

    def as_dict(self) -> dict:
        return {
            "source_execution_id": self.source_execution_id,
            "replay_execution_id": self.replay_execution_id,
            "workflow_id": self.workflow_id,
            "version": self.version,
            "status": self.status,
            "comparison": self.comparison.as_dict(),
        }


class ReplayService:
    """Replay a completed execution from its pinned workflow version."""

    def __init__(
        self,
        workflows: WorkflowStore,
        executions: ExecutionStore,
        audit: AuditStore | None = None,
    ) -> None:
        self.workflows = workflows
        self.executions = executions
        self.audit = audit

    def pinned_version(self, source_execution_id: str) -> tuple[dict, dict]:
        """Load ``(execution, version row)`` and verify the pin is intact."""
        execution = self.executions.get(source_execution_id)
        version = self.workflows.get_version(execution["workflow_id"], int(execution["version"]))
        if version is None:
            raise ReplayError(
                f"workflow {execution['workflow_id']} version {execution['version']}"
                " is not persisted; cannot replay"
            )
        if version["workflow_id"] != execution["workflow_id"] or int(version["version"]) != int(
            execution["version"]
        ):
            raise ReplayError("pinned version row does not match the execution")
        if dsl_hash(version["dsl_text"]) != version["dsl_hash"]:
            raise ReplayError(
                f"pinned workflow version {execution['version']} was modified; refusing replay"
            )
        return execution, version

    async def replay(
        self,
        source_execution_id: str,
        runner: Runner,
        *,
        role: object,
        replay_id: str = "",
    ) -> ReplayResult:
        """Re-execute the pinned version and report normalized-output match.

        Authorization is checked here (service layer, FR-02) in addition to
        any route dependency: knowing an execution id is never enough.
        """
        if not has_permission(role, Permission.WORKFLOW_RUN):
            raise PermissionError(f"role {role!r} lacks permission {Permission.WORKFLOW_RUN.value}")
        execution, version = self.pinned_version(source_execution_id)
        replay_id = replay_id or uuid.uuid4().hex
        run = await runner(version["dsl_text"], replay_id)
        run.run_id = replay_id
        stored = self.executions.record_run(
            execution["workflow_id"],
            int(execution["version"]),
            run,
            triggered_by="replay",
        )
        comparison = compare_outputs(
            _output_summaries(execution),
            _output_summaries(stored),
        )
        if self.audit is not None:
            AuditTrail(self.audit, source_execution_id).replay(
                source_execution_id, replay_id, match=comparison.match
            )
        return ReplayResult(
            source_execution_id=source_execution_id,
            replay_execution_id=str(stored["id"]),
            workflow_id=str(stored["workflow_id"]),
            version=int(stored["version"]),
            status=str(stored["status"]),
            comparison=comparison,
        )


def _output_summaries(execution: dict) -> dict[str, str]:
    """node_id -> latest non-empty output summary from an execution's steps."""
    summaries: dict[str, str] = {}
    for step in execution.get("steps", []):
        summary = str(step.get("output_summary", "") or "")
        if summary:
            summaries[str(step["node_id"])] = summary
    return summaries
