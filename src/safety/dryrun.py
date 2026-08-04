"""Phase 8 item [79]: the safe dry-run report.

A dry run answers "what would happen if I ran this?" without running
anything: the DSL goes through the same loader and validator as a real run,
and every node goes through the same policy service with the caller's role.
The report contains the verdict, the human explanation and the approval
quorum per node, plus a summary — and it is constructed by pure functions,
so there is no bus, no executor and no database involved.

The report also states what it did *not* do (``persisted``, ``dispatched``)
so a client cannot mistake a dry run for a real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.dsl.compiler import compile as compile_workflow
from engine.dsl.loader import DSLParseError, Workflow, load_text
from engine.executor.gate import flatten_node
from engine.validator.validator import validate

from .explanations import Explanation, explain
from .policy_service import ALLOW, DENY, NEED_APPROVAL, PolicyContext, PolicyService


@dataclass(frozen=True)
class DryRunNode:
    """One node's would-be verdict, in topological declaration order."""

    node_id: str
    node_type: str
    verdict: str
    explanation: Explanation

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "verdict": self.verdict,
            "explanation": self.explanation.as_dict(),
        }


@dataclass(frozen=True)
class DryRunReport:
    """The whole dry-run verdict: valid, per-node, and explicitly inert."""

    valid: bool
    name: str = ""
    version: int = 0
    nodes: tuple[DryRunNode, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    persisted: bool = False
    dispatched: bool = False

    @property
    def safe_to_run(self) -> bool:
        """True when no node is denied (gated nodes still pause and ask)."""
        return self.valid and self.counts.get(DENY, 0) == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "name": self.name,
            "version": self.version,
            "nodes": [node.as_dict() for node in self.nodes],
            "errors": list(self.errors),
            "counts": dict(self.counts),
            "safe_to_run": self.safe_to_run,
            "persisted": self.persisted,
            "dispatched": self.dispatched,
        }


def _error(stage: str, message: str, line: int, column: int) -> dict[str, Any]:
    return {
        "stage": stage,
        "message": str(message),
        "line": max(int(line), 1),
        "column": max(int(column), 1),
    }


def _counts(nodes: tuple[DryRunNode, ...]) -> dict[str, int]:
    counts = {ALLOW: 0, NEED_APPROVAL: 0, DENY: 0}
    for node in nodes:
        counts[node.verdict] = counts.get(node.verdict, 0) + 1
    return counts


def dry_run(
    dsl_text: str,
    *,
    dsl_type: str = "yaml",
    role: str = "OPERATOR",
    workspace_root: str = "",
) -> DryRunReport:
    """Validate + gate a workflow without persisting or dispatching anything."""
    try:
        workflow = load_text(dsl_text, dsl_type)
    except DSLParseError as exc:
        stage = "yaml" if dsl_type == "yaml" else "json"
        return DryRunReport(valid=False, errors=(_error(stage, str(exc), exc.line, exc.col),))

    errors = validate(workflow)
    if errors:
        return DryRunReport(
            valid=False,
            name=workflow.name,
            version=workflow.version,
            errors=tuple(_error(err.code, err.message, err.line, err.col) for err in errors),
        )

    nodes = _gate_all(workflow, role=role, workspace_root=workspace_root)
    return DryRunReport(
        valid=True,
        name=workflow.name,
        version=workflow.version,
        nodes=nodes,
        counts=_counts(nodes),
    )


def _gate_all(workflow: Workflow, *, role: str, workspace_root: str) -> tuple[DryRunNode, ...]:
    """Policy decision + explanation for every node, in compiled order."""
    dag = compile_workflow(workflow)
    service = PolicyService()
    context = PolicyContext(role=role, workspace_root=workspace_root)
    gated: list[DryRunNode] = []
    for node_id, node in dag["nodes"].items():
        decision = service.decide(node_id, flatten_node(node), context=context)
        gated.append(
            DryRunNode(
                node_id=node_id,
                node_type=str(node.get("type", "")),
                verdict=decision.verdict,
                explanation=explain(decision),
            )
        )
    return tuple(gated)
