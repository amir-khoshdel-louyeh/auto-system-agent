"""Phase 1 validator: schema → type → edge/ref → cycle → policy.

Own logic (no copy): the Kahn cycle stage mirrors the shape of
source_MIT/flow-forge `backend/main.py::topological_order` (returns None on
cycle); every other stage is our C9 grammar. Invalid DSL returns precise
`ValidationError`s with line/col and is never partially deployed.
"""

from __future__ import annotations

from ..dsl.compiler import normalize_edges
from ..dsl.grammar import ALLOWED_EVENTS, NODE_TYPES, REQUIRED_PARAMS, TRIGGER_KEYS
from ..dsl.loader import Workflow, find_line
from ..dsl.resolve import list_placeholders
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


def check_node_types(workflow: Workflow) -> list[ValidationError]:
    """Node-type stage: known type + required per-type params."""
    errors: list[ValidationError] = []
    for node in workflow.nodes:
        if node.type not in NODE_TYPES:
            errors.append(
                ValidationError(
                    "type",
                    f"node '{node.id}': unknown type '{node.type}', expected one of {list(NODE_TYPES)}",
                    *_loc(workflow, node.id or node.type or "nodes"),
                )
            )
            continue
        for key in REQUIRED_PARAMS.get(node.type, ()):
            if key not in node.params or node.params[key] in ("", None):
                errors.append(
                    ValidationError(
                        "type",
                        f"node '{node.id}' ({node.type}): missing required param '{key}'",
                        *_loc(workflow, node.id),
                    )
                )
        if node.type == "approval":
            quorum = node.params.get("quorum", 0)
            if not isinstance(quorum, int) or quorum < 1:
                errors.append(
                    ValidationError(
                        "type",
                        f"node '{node.id}' (approval): 'quorum' must be a positive integer",
                        *_loc(workflow, node.id),
                    )
                )
    for trigger in workflow.triggers:
        if isinstance(trigger, dict) and set(trigger) == {"event"}:
            if trigger["event"] not in ALLOWED_EVENTS:
                errors.append(
                    ValidationError(
                        "type",
                        f"unknown event '{trigger['event']}', expected one of {list(ALLOWED_EVENTS)}",
                        *_loc(workflow, str(trigger["event"])),
                    )
                )
    return errors


def check_edges(workflow: Workflow) -> list[ValidationError]:
    """Edge/ref stage: every edge endpoint, depends_on and ${ref} must resolve."""
    errors: list[ValidationError] = []
    known = {node.id for node in workflow.nodes if node.id}
    for src, dst in normalize_edges(workflow):
        for endpoint in (src, dst):
            if endpoint not in known:
                errors.append(
                    ValidationError(
                        "edge",
                        f"edge [{src!r}, {dst!r}]: unknown node ref '{endpoint}'",
                        *_loc(workflow, endpoint or "edges"),
                    )
                )
    for node in workflow.nodes:
        for path in list_placeholders(node.params):
            head = path.split(".")[0]
            if head not in known:
                errors.append(
                    ValidationError(
                        "edge",
                        f"node '{node.id}': unknown placeholder ref '${{{path}}}'",
                        *_loc(workflow, node.id),
                    )
                )
    return errors


def validate(workflow: Workflow) -> list[ValidationError]:
    """Run all stages in order; empty list means the workflow is valid."""
    errors: list[ValidationError] = []
    errors.extend(check_schema(workflow))
    errors.extend(check_node_types(workflow))
    errors.extend(check_edges(workflow))
    # Next commit appends: check_cycle, check_policy.
    return errors


def is_valid(workflow: Workflow) -> bool:
    """True when `validate` reports no errors."""
    return not validate(workflow)
