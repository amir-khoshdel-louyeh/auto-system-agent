"""Phase 8 item [78]: human-readable policy explanations (FR-07).

The policy service answers with a verdict, a score, reasons and a quorum.
That is the right shape for code and the wrong shape for a person deciding
whether to approve a node. :func:`explain` turns one decision into three
sentences — what happened, why, and what the caller can do next — without
inventing a second decision: the verdict always comes from the same
:class:`~safety.policy_service.PolicyService`.

Explanations are deterministic and side-effect free, so the API, the client
and tests all render exactly the same words for the same decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .policy_service import ALLOW, DENY, LOW_MAX, NEED_APPROVAL, PolicyDecision, PolicyService

#: Risk bands as words a person can act on.
BANDS: tuple[tuple[int, str], ...] = (
    (LOW_MAX, "low"),
    (100, "high"),
)


@dataclass(frozen=True)
class Explanation:
    """One decision rendered for a human (headline + why + next step)."""

    node_id: str
    verdict: str
    headline: str
    detail: str
    next_step: str
    score: int
    quorum: int
    policy_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "verdict": self.verdict,
            "headline": self.headline,
            "detail": self.detail,
            "next_step": self.next_step,
            "score": self.score,
            "quorum": self.quorum,
            "policy_version": self.policy_version,
        }


def risk_band(score: int) -> str:
    """``low`` / ``high`` band for a 0..100 risk score."""
    for ceiling, word in BANDS:
        if score <= ceiling:
            return word
    return "high"


def _detail(decision: PolicyDecision) -> str:
    """The reasons as one readable sentence, or a neutral fallback."""
    reasons = [reason.strip().rstrip(".") for reason in decision.reasons if reason.strip()]
    if not reasons:
        return "No risk signals were raised for this node."
    if len(reasons) == 1:
        return f"Reason: {reasons[0]}."
    return "Reasons: " + "; ".join(reasons) + "."


def _headline(decision: PolicyDecision) -> str:
    band = risk_band(decision.score)
    if decision.verdict == ALLOW:
        return f"Allowed — {band} risk (score {decision.score}/100)."
    if decision.verdict == NEED_APPROVAL:
        approvers = "one approver" if decision.quorum <= 1 else f"{decision.quorum} approvers"
        return f"Needs approval — {approvers} required (score {decision.score}/100)."
    if decision.verdict == DENY:
        return f"Denied — {band} risk (score {decision.score}/100)."
    return f"Unknown verdict {decision.verdict!r} (score {decision.score}/100)."


def _next_step(decision: PolicyDecision) -> str:
    if decision.verdict == ALLOW:
        return "No action needed: the node can run as written."
    if decision.verdict == NEED_APPROVAL:
        approvers = "an approver" if decision.quorum <= 1 else f"{decision.quorum} approvers"
        return f"Ask {approvers} to review and decide; the run pauses until then."
    if decision.verdict == DENY:
        return (
            "Rewrite the node so it stays inside the workspace and avoids "
            "destructive operations; a denial cannot be approved."
        )
    return "Treat this as denied and inspect the policy version."


def explain(decision: PolicyDecision | dict[str, Any]) -> Explanation:
    """Render one policy decision as a human-readable explanation."""
    if not isinstance(decision, PolicyDecision):
        decision = PolicyDecision(
            node_id=str(decision.get("node_id", "")),
            node_type=str(decision.get("node_type", "")),
            verdict=str(decision.get("verdict", DENY)),
            score=int(decision.get("score", 100)),
            reasons=tuple(str(reason) for reason in decision.get("reasons", [])),
            quorum=int(decision.get("quorum", 0)),
            policy_version=str(decision.get("policy_version", "")),
        )
    return Explanation(
        node_id=decision.node_id,
        verdict=decision.verdict,
        headline=_headline(decision),
        detail=_detail(decision),
        next_step=_next_step(decision),
        score=decision.score,
        quorum=decision.quorum,
        policy_version=decision.policy_version,
    )


def explain_command(
    command: str, *, node_id: str = "command", role: str = "OPERATOR"
) -> Explanation:
    """Explain the gate verdict for one shell command (no execution)."""
    from .policy_service import PolicyContext

    decision = PolicyService().decide(
        node_id, {"type": "shell", "run": command}, context=PolicyContext(role=role)
    )
    return explain(decision)
