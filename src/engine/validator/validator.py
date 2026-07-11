"""Phase 1 validator: schema → type → edge/ref → cycle → policy.

Own logic (no copy): the Kahn cycle stage mirrors the shape of
source_MIT/flow-forge `backend/main.py::topological_order` (returns None on
cycle); every other stage is our C9 grammar. Invalid DSL returns precise
`ValidationError`s with line/col and is never partially deployed.
"""

from __future__ import annotations

from ..dsl.grammar import TRIGGER_KEYS
from ..dsl.loader import Workflow, find_line
from .errors import ValidationError


def _loc(workflow: Workflow, needle: str) -> tuple[int, int]:
    if workflow.raw_text and needle:
        line, col = find_line(workflow.raw_text, needle)
        if line:
            return (line, col)
    return (1, 1)


def check_schema(workflow: Workflow) -> list[ValidationError]:
    """Top-level shape: name/version/nodes/triggers/edges/policy types."""
    errors: list[ValidationError] = []
    if not workflow.name or not isinstance(workflow.name, str):
        errors.append(
            ValidationError("schema", "missing required key 'workflow' (non-empty string)", *_loc(workflow, "workflow"))
        )
    if not isinstance(workflow.version, int) or workflow.version < 1:
        errors.append(
            ValidationError("schema", "key 'version' must be a positive integer", *_loc(workflow, "version"))
        )
    if not workflow.nodes:
        errors.append(
            ValidationError("schema", "key 'nodes' must be a non-empty list", *_loc(workflow, "nodes"))
        )
    for node in workflow.nodes:
        if not node.id:
            errors.append(
                ValidationError("schema", "every node needs a non-empty string 'id'", *_loc(workflow, "id"))
            )
        if not node.type:
            errors.append(
                ValidationError(
                    "schema",
                    f"node '{node.id or '?'}' is missing required key 'type'",
                    *_loc(workflow, node.id or "type"),
                )
            )
    for trigger in workflow.triggers:
        if not isinstance(trigger, dict) or set(trigger) - set(TRIGGER_KEYS) or len(trigger) != 1:
            errors.append(
                ValidationError(
                    "schema",
                    f"each trigger must be exactly one of {list(TRIGGER_KEYS)}: {trigger!r}",
                    *_loc(workflow, "triggers"),
                )
            )
    for src, dst in workflow.edges:
        if not src or not dst:
            errors.append(
                ValidationError("schema", f"each edge needs [from, to]: [{src!r}, {dst!r}]", *_loc(workflow, "edges"))
            )
    return errors


def validate(workflow: Workflow) -> list[ValidationError]:
    """Run all stages in order; empty list means the workflow is valid."""
    errors: list[ValidationError] = []
    errors.extend(check_schema(workflow))
    # Next commits append: check_node_types, check_edges, check_cycle, check_policy.
    return errors


def is_valid(workflow: Workflow) -> bool:
    """True when `validate` reports no errors."""
    return not validate(workflow)
