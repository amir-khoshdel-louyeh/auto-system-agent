"""Phase 7 item [70]: memory lifecycle states (FR-15).

    QUARANTINED -> SANDBOX -> PUBLIC

- ``QUARANTINED``: a new plan. It exists, but it cannot be reused: the only
  way out is to run it in the sandbox and *succeed*.
- ``SANDBOX``: proven once in the sandbox; still not published for anyone.
- ``PUBLIC``: promoted after the configured proof (successful sandbox runs
  plus the reputation threshold), so it may be reused directly.

A failed or denied plan stays where it is (a denied plan cannot jump to
``PUBLIC``), which is the "failed plan cannot be auto-reused" rule of the
Phase 7 gate. Transitions are checked so an illegal jump is a bug in tests
rather than a silently reusable plan.
"""

from __future__ import annotations

QUARANTINED = "QUARANTINED"
SANDBOX = "SANDBOX"
PUBLIC = "PUBLIC"

#: Every state, in promotion order (frozen vocabulary).
ALL_STATES: tuple[str, ...] = (QUARANTINED, SANDBOX, PUBLIC)

#: States a retrieval result may return (quarantine is never directly reusable).
REUSABLE_STATES: tuple[str, ...] = (PUBLIC,)

#: Legal transitions.
ALLOWED: dict[str, frozenset[str]] = {
    QUARANTINED: frozenset({SANDBOX, QUARANTINED}),
    SANDBOX: frozenset({PUBLIC, SANDBOX, QUARANTINED}),
    PUBLIC: frozenset({QUARANTINED}),
}

#: Successful sandbox runs required before promotion is even considered.
SANDBOX_PROOFS_REQUIRED = 1
#: Minimum reputation a sandboxed plan needs to go public (FR-15).
PUBLICATION_REPUTATION = 60.0


class InvalidStateTransition(ValueError):
    """A memory item was asked to make a transition the lifecycle forbids."""


def is_reusable(state: str) -> bool:
    """True only for states a reuse proposal may be built from."""
    return state in REUSABLE_STATES


def can_transition(src: str, dst: str) -> bool:
    """True when ``src -> dst`` is a legal memory state change."""
    return dst in ALLOWED.get(src, frozenset())


def require_transition(src: str, dst: str) -> None:
    """Raise :class:`InvalidStateTransition` unless ``src -> dst`` is legal."""
    if not can_transition(src, dst):
        raise InvalidStateTransition(f"illegal memory transition {src!r} -> {dst!r}")


def next_state(state: str, *, sandbox_proofs: int, reputation: float, last_outcome_ok: bool) -> str:
    """The state a plan is allowed to reach after one recorded episode.

    - a failed/denied episode keeps the plan where it is (no promotion);
    - a successful sandbox run moves ``QUARANTINED`` to ``SANDBOX``;
    - ``SANDBOX`` becomes ``PUBLIC`` once the proof count and reputation
      threshold are met, and a public plan that fails again is quarantined.
    """
    if not last_outcome_ok:
        return QUARANTINED if state != QUARANTINED else QUARANTINED
    if state == QUARANTINED:
        return SANDBOX
    if state == SANDBOX and (
        sandbox_proofs >= SANDBOX_PROOFS_REQUIRED and reputation >= PUBLICATION_REPUTATION
    ):
        return PUBLIC
    return state
