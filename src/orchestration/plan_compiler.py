"""Phase 6 gate: compile a planned workflow through the shared DSL path.

A plan is only useful if it is accepted by the *same* pipeline as a
hand-authored workflow: the plan document is serialised to DSL text, parsed
by :mod:`engine.dsl.loader`, checked by :mod:`engine.validator.validator`
(schema → type → edge → cycle → policy) and, at run time, gated by the same
:class:`~safety.policy_service.PolicyService` as every other node.

Nothing here is planner-specific: no plan can skip validation, and an
invalid plan is rejected as a whole (never partially deployed).
"""

from __future__ import annotations

import json
from typing import Any

from engine.dsl.loader import Workflow, load_text
from engine.executor.gate import flatten_node
from engine.validator.validator import validate
from safety.policy_service import PolicyService

from .intents import Plan

DEFAULT_WORKFLOW_NAME = "planned-workflow"


class PlanRejected(ValueError):
    """A plan did not survive validation (stage + message of the first error)."""

    def __init__(self, errors: list[Any]) -> None:
        first = errors[0]
        super().__init__(f"[{first.code}] {first.message} (line {first.line}, column {first.col})")
        self.errors = list(errors)


def plan_document(
    plan: Plan,
    *,
    name: str = DEFAULT_WORKFLOW_NAME,
    version: int = 1,
    triggers: list[Any] | None = None,
) -> dict[str, Any]:
    """The DSL document for a plan (raises ValueError when not executable)."""
    return plan.to_document(name, version=version, triggers=triggers)


def compile_plan(
    plan: Plan,
    *,
    name: str = DEFAULT_WORKFLOW_NAME,
    version: int = 1,
    triggers: list[Any] | None = None,
) -> Workflow:
    """Validate a plan's DSL document and return the parsed workflow."""
    document = plan_document(plan, name=name, version=version, triggers=triggers)
    workflow = load_text(json.dumps(document), "json")
    errors = validate(workflow)
    if errors:
        raise PlanRejected(errors)
    return workflow


def policy_verdicts(workflow: Workflow) -> dict[str, str]:
    """Policy verdict per node of a compiled workflow (gate evidence)."""
    service = PolicyService()
    return {
        node.id: service.decide(
            node.id, flatten_node({"type": node.type, "params": dict(node.params)})
        ).verdict
        for node in workflow.nodes
    }


def plan_policy_verdicts(plan: Plan, **kwargs: Any) -> dict[str, str]:
    """Convenience: compile a plan and return its per-node policy verdicts."""
    return policy_verdicts(compile_plan(plan, **kwargs))
