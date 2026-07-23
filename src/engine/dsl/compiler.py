"""Compiler: Workflow model -> versioned DAG dict (no execution).

Builds the adjacency view the validator and (phase 2) executor share.
Normalization covers both canonical `edges: [[src, dst]]` and legacy
upstream-style `depends_on` declared inside node params (accepted at load,
compiled to the same edges).
"""

from __future__ import annotations

from typing import Any

from .loader import Workflow


def normalize_edges(workflow: Workflow) -> list[tuple[str, str]]:
    """Canonical edge list: explicit edges + `depends_on` params as edges."""
    edges = list(workflow.edges)
    for node in workflow.nodes:
        deps = node.params.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if isinstance(deps, (list, tuple)):
            for dep in deps:
                if isinstance(dep, str) and dep:
                    edges.append((dep, node.id))
    # Deduplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for edge in edges:
        if edge not in seen:
            seen.add(edge)
            out.append(edge)
    return out


def compile(workflow: Workflow) -> dict[str, Any]:
    """Return `{name, version, nodes, adjacency, triggers, policy}`."""
    nodes = {node.id: {"type": node.type, "params": dict(node.params)} for node in workflow.nodes}
    edges = normalize_edges(workflow)
    adjacency: dict[str, list[str]] = {nid: [] for nid in nodes}
    for src, dst in edges:
        if src in adjacency and dst in nodes:
            adjacency[src].append(dst)
    return {
        "name": workflow.name,
        "version": workflow.version,
        "nodes": nodes,
        "edges": edges,
        "adjacency": adjacency,
        "triggers": list(workflow.triggers),
        "policy": dict(workflow.policy),
    }
