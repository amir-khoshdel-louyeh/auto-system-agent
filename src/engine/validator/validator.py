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
            ValidationError(
                "schema",
                "missing required key 'workflow' (non-empty string)",
                *_loc(workflow, "workflow"),
            )
        )
    if not isinstance(workflow.version, int) or workflow.version < 1:
        errors.append(
            ValidationError(
                "schema", "key 'version' must be a positive integer", *_loc(workflow, "version")
            )
        )
    if not workflow.nodes:
        errors.append(
            ValidationError(
                "schema", "key 'nodes' must be a non-empty list", *_loc(workflow, "nodes")
            )
        )
    for node in workflow.nodes:
        if not node.id:
            errors.append(
                ValidationError(
                    "schema", "every node needs a non-empty string 'id'", *_loc(workflow, "id")
                )
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
    # Container types from the original mapping: the loader coerces wrong
    # types to empty defaults, which would silently hide authoring bugs.
    for key, expected in (("triggers", list), ("nodes", list), ("edges", list), ("policy", dict)):
        if key in workflow.raw and not isinstance(workflow.raw[key], expected):
            errors.append(
                ValidationError(
                    "schema",
                    f"key '{key}' must be a {expected.__name__},"
                    f" got {type(workflow.raw[key]).__name__}",
                    *_loc(workflow, key),
                )
            )
    if "version" in workflow.raw and not isinstance(workflow.raw["version"], int):
        errors.append(
            ValidationError(
                "schema",
                f"key 'version' must be an integer, got {type(workflow.raw['version']).__name__}",
                *_loc(workflow, "version"),
            )
        )
    for src, dst in workflow.edges:
        if not src or not dst:
            errors.append(
                ValidationError(
                    "schema",
                    f"each edge needs [from, to]: [{src!r}, {dst!r}]",
                    *_loc(workflow, "edges"),
                )
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
            elif key != "quorum" and not isinstance(node.params[key], str):
                errors.append(
                    ValidationError(
                        "type",
                        f"node '{node.id}' ({node.type}): param '{key}' must be a string,"
                        f" got {type(node.params[key]).__name__}",
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
        if not isinstance(trigger, dict) or len(trigger) != 1:
            continue  # schema stage owns the shape complaint
        kind, value = next(iter(trigger.items()))
        if kind == "manual" and not isinstance(value, bool):
            errors.append(
                ValidationError(
                    "type",
                    f"trigger 'manual' must be true (got {value!r})",
                    *_loc(workflow, "manual"),
                )
            )
        elif kind == "cron" and (not isinstance(value, str) or not value.strip()):
            errors.append(
                ValidationError(
                    "type",
                    "trigger 'cron' must be a non-empty cron expression string",
                    *_loc(workflow, "cron"),
                )
            )
        elif kind == "event":
            if not isinstance(value, str) or value not in ALLOWED_EVENTS:
                errors.append(
                    ValidationError(
                        "type",
                        f"unknown event {value!r}, expected one of {list(ALLOWED_EVENTS)}",
                        *_loc(workflow, str(value)),
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


def topological_order(workflow: Workflow) -> list[str] | None:
    """Kahn's algorithm; returns None when a cycle exists.

    Same shape as source_MIT/flow-forge `backend/main.py::topological_order`.
    """
    nodes = {node.id for node in workflow.nodes if node.id}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for src, dst in normalize_edges(workflow):
        if src in adj and dst in nodes:
            adj[src].append(dst)
    in_degree = {nid: 0 for nid in nodes}
    for targets in adj.values():
        for tgt in targets:
            in_degree[tgt] += 1
    queue = sorted(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for nxt in adj.get(current, []):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    return order if len(order) == len(nodes) else None


def check_cycle(workflow: Workflow) -> list[ValidationError]:
    """Cycle stage: the edge graph must be a DAG."""
    if topological_order(workflow) is None:
        guilty = next((n.id for n in workflow.nodes if n.id), "nodes")
        return [
            ValidationError(
                "cycle", "workflow contains a cycle; cannot execute", *_loc(workflow, guilty)
            )
        ]
    return []


#: Shell fragments that make a node dangerous (policy gate via C6).
DANGEROUS_SHELL_PATTERNS: tuple[str, ...] = (
    "rm -rf",
    "mkfs",
    "dd if=",
    "dd of=",
    ":(){:|:&};:",
    "shutdown",
    "reboot",
    "format ",
    "sudo rm",
    "chmod -R 777 /",
    "del /f",
)


def _is_dangerous(node_type: str, params: dict) -> bool:
    if node_type == "custom":
        return True
    if node_type == "shell":
        run = str(params.get("run", "")).lower()
        return any(pat in run for pat in DANGEROUS_SHELL_PATTERNS)
    return False


def _ancestors(workflow: Workflow, target: str) -> set[str]:
    parents: dict[str, list[str]] = {}
    for src, dst in normalize_edges(workflow):
        parents.setdefault(dst, []).append(src)
    seen: set[str] = set()
    stack = list(parents.get(target, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(parents.get(current, []))
    return seen


def check_policy(workflow: Workflow) -> list[ValidationError]:
    """Policy stage: dangerous nodes need an approval ancestor (C6 gate)."""
    errors: list[ValidationError] = []
    approvals = {n.id for n in workflow.nodes if n.type == "approval"}
    for node in workflow.nodes:
        if _is_dangerous(node.type, node.params):
            if not approvals:
                errors.append(
                    ValidationError(
                        "policy",
                        f"node '{node.id}' is dangerous but the workflow has no approval node",
                        *_loc(workflow, node.id),
                    )
                )
            elif not (_ancestors(workflow, node.id) & approvals):
                errors.append(
                    ValidationError(
                        "policy",
                        f"node '{node.id}' is dangerous without an approval gate on its path",
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
    errors.extend(check_cycle(workflow))
    errors.extend(check_policy(workflow))
    return errors


def is_valid(workflow: Workflow) -> bool:
    """True when `validate` reports no errors."""
    return not validate(workflow)
