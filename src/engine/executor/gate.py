"""Policy gate: thin adapter over `safety.policy_service` (item [33], FR-07).

The decision logic — ``ALLOW`` / ``NEED_APPROVAL`` / ``DENY`` plus score,
reasons, required quorum and policy version — lives in
:mod:`safety.policy_service`. This module keeps the scheduler-facing surface
stable: an async ``decide(node_id, spec)`` returning one verdict string, and
the ``decide_for_command`` helper used by shell tooling.
"""

from __future__ import annotations

from typing import Any

from safety.policy_service import (
    ALLOW,
    DENY,
    NEED_APPROVAL,
    PolicyService,
    looks_obfuscated,
)

__all__ = [
    "ALLOW",
    "NEED_APPROVAL",
    "DENY",
    "decide",
    "decide_for_command",
    "flatten_node",
    "looks_obfuscated",
]

_SERVICE = PolicyService()


def flatten_node(spec: dict[str, Any]) -> dict[str, Any]:
    """Flatten a compiled node's `params` so the policy service sees its fields.

    The compiled DAG nests node parameters (``{"type": "shell", "params":
    {"run": "..."}}``) while :class:`~safety.policy_service.PolicyService`
    reads a flat spec. Without this normalisation the scheduler gate would
    inspect an *empty* command and allow a dangerous node through; the shell
    worker re-checks, but the gate must fail closed first (item [34]).
    """
    if not isinstance(spec, dict):
        return {}
    params = spec.get("params")
    if not isinstance(params, dict):
        return dict(spec)
    merged = {key: value for key, value in spec.items() if key != "params"}
    merged.update(params)
    return merged


def decide_for_command(command: str) -> tuple[str, str]:
    """Gate one shell command; returns (verdict, reason)."""
    decision = _SERVICE.decide("", {"type": "shell", "run": command})
    reason = "; ".join(decision.reasons) if decision.reasons else decision.verdict
    return (decision.verdict, reason)


async def decide(node_id: str, spec: dict[str, Any]) -> str:
    """Scheduler gate entry: verdict for one compiled node spec."""
    return _SERVICE.decide(node_id, flatten_node(spec)).verdict
