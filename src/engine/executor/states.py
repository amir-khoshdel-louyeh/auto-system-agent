"""Node lifecycle states for the DAG executor (item [29], proposal §10.4).

The scheduler reports terminal outcomes as ``OK`` / ``FAILED`` / ``SKIPPED``
/ ``CANCELLED``; the durable run model speaks the proposal's state names.
This module is the single vocabulary both sides share: the frozen states,
the legal transitions and the mapping from an executor node status.

    PENDING -> READY -> RUNNING -> SUCCEEDED
                             |-> RETRYING -> RUNNING
                             |-> FAILED   -> COMPENSATING -> COMPENSATED
                             |-> BLOCKED

``BLOCKED`` is what a node becomes when a dependency failed (no attempt was
made); a policy ``DENY`` or exhausted retries reach ``FAILED``. Transitions
are checked so an illegal state change is a bug we can catch in tests rather
than a silent inconsistency in the audit trail.
"""

from __future__ import annotations

from .result import CANCELLED, OK, SKIPPED

PENDING = "PENDING"
READY = "READY"
RUNNING = "RUNNING"
RETRYING = "RETRYING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
COMPENSATING = "COMPENSATING"
COMPENSATED = "COMPENSATED"

#: States a node never leaves.
TERMINAL: frozenset[str] = frozenset({SUCCEEDED, FAILED, BLOCKED, COMPENSATED})

#: Every node state name, in lifecycle order (frozen vocabulary).
ALL_STATES: tuple[str, ...] = (
    PENDING,
    READY,
    RUNNING,
    RETRYING,
    SUCCEEDED,
    FAILED,
    BLOCKED,
    COMPENSATING,
    COMPENSATED,
)

#: Legal transitions out of every state.
ALLOWED: dict[str, frozenset[str]] = {
    PENDING: frozenset({READY, BLOCKED}),
    READY: frozenset({RUNNING, BLOCKED}),
    RUNNING: frozenset({SUCCEEDED, RETRYING, FAILED, COMPENSATING}),
    RETRYING: frozenset({RUNNING, FAILED}),
    FAILED: frozenset({COMPENSATING}),
    COMPENSATING: frozenset({COMPENSATED}),
    SUCCEEDED: frozenset(),
    BLOCKED: frozenset(),
    COMPENSATED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a node is asked to make a state change the machine forbids."""


def is_terminal(state: str) -> bool:
    """True for states a node never leaves once entered."""
    return state in TERMINAL


def can_transition(src: str, dst: str) -> bool:
    """True when ``src -> dst`` is a legal node state change."""
    return dst in ALLOWED.get(src, frozenset())


def require_transition(src: str, dst: str) -> None:
    """Raise :class:`InvalidTransition` unless ``src -> dst`` is legal."""
    if not can_transition(src, dst):
        raise InvalidTransition(f"illegal node transition {src!r} -> {dst!r}")


def state_for(node_status: str) -> str:
    """Map an executor node status onto the durable run-state vocabulary.

    - ``OK`` -> ``SUCCEEDED``
    - ``SKIPPED`` -> ``BLOCKED`` (a dependency never satisfied it)
    - ``FAILED`` / ``CANCELLED`` -> ``FAILED`` (cancellation is terminal)
    """
    if node_status == OK:
        return SUCCEEDED
    if node_status == SKIPPED:
        return BLOCKED
    if node_status == CANCELLED:
        return FAILED
    return FAILED
