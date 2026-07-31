"""Step-worker core: request/reply over `steps:<type>` → `steps:results`.

One Runner per node.type consumes `step.dispatched` tasks and verdicts with
`step.finished` / `step.failed` on the shared results stream. Verdicts carry
the run correlation id and per-node idempotency keys, so dispatcher waits
and replays stay deterministic.

Item [43]: an optional ``persist`` callback writes the step's durable state
*before* the verdict is published — and therefore before the delivery is
acknowledged. A crash between execution and audit can then only leave an
extra durable step row, never a lost effect; if persistence itself fails, the
handler raises and the delivery is retried / dead-lettered instead of acked.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..backend import Backend
from ..consumer import Runner
from ..events import new_event

RESULTS_STREAM = "steps:results"

#: (run_id, node_id, state, error, output) -> None. Called before the verdict.
PersistStep = Callable[[str, str, str, str, dict[str, Any]], None]


class PolicyDenied(RuntimeError):
    """Worker refused a node the policy gate DENYed."""


class ApprovalRequired(RuntimeError):
    """Worker parked a node that needs a human quorum (phase 6)."""


def steps_stream(node_type: str) -> str:
    """Task stream one worker subscribes: `steps:<node.type>`."""
    return f"steps:{node_type}"


def make_step_worker(
    backend: Backend,
    group: str,
    node_type: str,
    exec_fn,
    *,
    max_retries: int = 2,
    persist: PersistStep | None = None,
) -> Runner:
    """Build the Runner that serves one node.type (exec_fn may raise)."""
    runner = Runner(
        backend, group, [steps_stream(node_type)], max_retries=max_retries, base_delay_ms=10
    )

    @runner.on("step.dispatched")
    async def handle(event) -> None:
        run_id = event.correlation_id
        node_id = event.data.get("node_id", "")
        node = event.data.get("node", {})
        if not isinstance(node, dict) or node.get("type") != node_type:
            return  # not ours; ack and leave for the right worker
        ctx = {"backend": backend, "run_id": run_id, "data": event.data}
        try:
            output = await exec_fn(node_id, node, ctx)
        except Exception as exc:  # noqa: BLE001 - worker failure is a verdict
            error = str(exc)[:2000]
            if persist is not None:
                persist(run_id, node_id, "FAILED", error, {})
            await backend.publish(
                RESULTS_STREAM,
                new_event(
                    "step.failed",
                    {"node_id": node_id, "error": error},
                    correlation_id=run_id,
                    idempotency_key=f"{run_id}:{node_id}:failed",
                ),
            )
            return
        if not isinstance(output, dict):
            output = {"output": output}
        # Durable state first (item [43]): the verdict is only published — and
        # the delivery only acknowledged — once the step row exists.
        if persist is not None:
            persist(run_id, node_id, "SUCCEEDED", "", output)
        await backend.publish(
            RESULTS_STREAM,
            new_event(
                "step.finished",
                {"node_id": node_id, "output": output},
                correlation_id=run_id,
                idempotency_key=f"{run_id}:{node_id}:finished",
            ),
        )

    return runner
