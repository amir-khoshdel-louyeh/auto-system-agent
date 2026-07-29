"""Phase 1 DSL loader (adapted, src-only).

Adapts source_MIT/llm-flow-engine `llm_flow_engine/dsl_loader.py` to our
frozen C9 grammar (proposal §6 C9)::

    workflow: onboarding-demo
    version: 3
    triggers: [{manual|cron|event: file.arrived} ...]
    nodes: [{id, type, ...params}]
    edges: [[src, dst], ...]
    policy: {max_retries, on_ground_fail, ...}

Differences from upstream (deliberate):
- upstream speaks `metadata/input/executors/output` with a `func_map` of
  callables; we speak versioned `nodes/edges` with plain data params
  (execution arrives in phase 2, no callables at load time).
- upstream needs `loguru`; we use stdlib only.
- upstream resolves `${...}` placeholders against a global context at load;
  placeholder expansion lives in `resolve.py` (next commit), not here.

This module only parses text/dict into plain data models. Semantic checks
(schema/type/edge/cycle/policy) live in `engine.validator`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - env without PyYAML
    yaml = None


@dataclass(frozen=True)
class Node:
    """One workflow step; extra keys hold per-type params (run/target/...)."""

    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Workflow:
    """Parsed workflow document (unvalidated; see validator)."""

    name: str
    version: int
    triggers: list[Any] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""


def parse_text(text: str, dsl_type: str = "yaml") -> dict[str, Any]:
    """Parse DSL text into a plain dict (yaml default, json optional)."""
    if dsl_type == "yaml":
        if yaml is None:
            raise ImportError("PyYAML is required for YAML DSL: pip install pyyaml")
        obj = yaml.safe_load(text)
    elif dsl_type == "json":
        obj = json.loads(text)
    else:
        raise ValueError("dsl_type must be 'yaml' or 'json'")
    if not isinstance(obj, dict):
        raise ValueError("DSL top-level must be a mapping")
    return obj


def _as_nodes(raw: Any) -> list[Node]:
    items = raw if isinstance(raw, list) else []
    out: list[Node] = []
    for entry in items:
        if not isinstance(entry, dict):
            out.append(Node(id="", type="", params={}))
            continue
        nid = entry.get("id", "")
        ntype = entry.get("type", "")
        params = {k: v for k, v in entry.items() if k not in ("id", "type")}
        out.append(
            Node(
                id=nid if isinstance(nid, str) else "",
                type=ntype if isinstance(ntype, str) else "",
                params=params if isinstance(params, dict) else {},
            )
        )
    return out


def _as_edges(raw: Any) -> list[tuple[str, str]]:
    items = raw if isinstance(raw, list) else []
    out: list[tuple[str, str]] = []
    for entry in items:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            src, dst = entry
            out.append((str(src), str(dst)))
        elif isinstance(entry, dict) and "from" in entry and "to" in entry:
            out.append((str(entry["from"]), str(entry["to"])))
        else:
            out.append(("", ""))
    return out


def load_workflow(obj: dict[str, Any], raw_text: str = "") -> Workflow:
    """Build a Workflow from a parsed dict (no semantic validation yet)."""
    name = obj.get("workflow", "")
    version = obj.get("version", 0)
    triggers = obj.get("triggers", [])
    policy = obj.get("policy", {})
    return Workflow(
        name=name if isinstance(name, str) else "",
        version=version if isinstance(version, int) else 0,
        triggers=triggers if isinstance(triggers, list) else [],
        nodes=_as_nodes(obj.get("nodes", [])),
        edges=_as_edges(obj.get("edges", [])),
        policy=policy if isinstance(policy, dict) else {},
        raw_text=raw_text,
    )


def load_text(text: str, dsl_type: str = "yaml") -> Workflow:
    """Parse text and return the Workflow model (keeps raw text for line/col)."""
    return load_workflow(parse_text(text, dsl_type), raw_text=text)


def find_line(raw_text: str, needle: str) -> tuple[int, int]:
    """Return 1-based (line, col) of first `needle` occurrence; (0, 0) if absent."""
    for lineno, line in enumerate(raw_text.splitlines(), start=1):
        col = line.find(needle)
        if col >= 0:
            return (lineno, col + 1)
    return (0, 0)
