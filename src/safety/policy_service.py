"""Policy service: one deterministic decision before any node runs (item [33]).

Owns FR-07: every executable node gets exactly one of ``ALLOW`` /
``NEED_APPROVAL`` / ``DENY`` with a numeric risk score, human-readable
reasons, the required approval quorum and the policy version. The executor's
gate is a thin adapter over this service, so authorization is decided in one
place and can be unit-tested without a scheduler or a database.

Risk thresholds are configuration (module constants), not scattered magic
numbers, and an unknown node type fails closed to ``DENY``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from safety.command_guard import assess_command

ALLOW = "ALLOW"
NEED_APPROVAL = "NEED_APPROVAL"
DENY = "DENY"

#: Bump when the decision rules change; recorded with every decision (§FR-07).
POLICY_VERSION = "policy-1"

#: Command-guard verdict thresholds mapped to policy bands.
LOW_MAX = 30
MEDIUM_MAX = 70

#: One approval for these types, two people for SDK/custom code.
_QUORUM_GATED = 1
_QUORUM_HIGH = 2

#: Node types that mutate OS state or run SDK code -> always human-gated.
_GATED_TYPES = ("gui_click", "gui_type", "custom")
_COMMAND_TYPES = ("shell",)

#: A single alphanumeric/blob token this long smells obfuscated (base64).
_BLOB_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")


def looks_obfuscated(command: str) -> bool:
    """Light anomaly: True when the command embeds a long encoded blob."""
    return bool(_BLOB_RE.search(command or ""))


@dataclass(frozen=True)
class PolicyDecision:
    """One authorization decision (FR-07)."""

    node_id: str
    node_type: str
    verdict: str
    score: int
    reasons: tuple[str, ...]
    quorum: int
    policy_version: str = POLICY_VERSION

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.verdict == NEED_APPROVAL

    @property
    def denied(self) -> bool:
        return self.verdict == DENY

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "verdict": self.verdict,
            "score": self.score,
            "reasons": list(self.reasons),
            "quorum": self.quorum,
            "policy_version": self.policy_version,
        }


class PolicyService:
    """Deterministic policy engine shared by the API, executor and workers."""

    def __init__(self, *, policy_version: str = POLICY_VERSION) -> None:
        self.policy_version = policy_version

    def decide(self, node_id: str, spec: dict[str, Any]) -> PolicyDecision:
        """Return the frozen decision for one node spec."""
        node_type = str(spec.get("type", ""))
        if node_type in _COMMAND_TYPES:
            return self._decide_command(node_id, node_type, str(spec.get("run", "")))
        if node_type == "approval":
            # The human quorum itself is the approval worker's job; the gate
            # must let the approval node run so it can collect it.
            return self._make(node_id, node_type, ALLOW, 0, ("approval node",), 0)
        if node_type in _GATED_TYPES:
            quorum = _QUORUM_HIGH if node_type == "custom" else _QUORUM_GATED
            return self._make(
                node_id,
                node_type,
                NEED_APPROVAL,
                MEDIUM_MAX,
                (f"{node_type} mutates OS state or runs SDK code",),
                quorum,
            )
        # Unknown / missing node type: fail closed.
        return self._make(
            node_id, node_type or "?", DENY, 100, ("unknown node type",), 0
        )

    def _decide_command(self, node_id: str, node_type: str, command: str) -> PolicyDecision:
        assessment = assess_command(command)
        score = int(assessment.get("score", 0))
        reasons = tuple(assessment.get("reasons", []))
        verdict = assessment.get("verdict", "DENY")
        if verdict == "DENY":
            return self._make(node_id, node_type, DENY, max(score, MEDIUM_MAX + 1), reasons, 0)
        if verdict == "CONFIRM":
            return self._make(node_id, node_type, NEED_APPROVAL, score, reasons, 1)
        # ALLOW band: a suspicious payload still escalates to a single approver.
        if looks_obfuscated(command):
            return self._make(
                node_id,
                node_type,
                NEED_APPROVAL,
                score,
                reasons + ("anomaly: long encoded blob in command",),
                1,
            )
        return self._make(node_id, node_type, ALLOW, score, reasons, 0)

    def _make(
        self,
        node_id: str,
        node_type: str,
        verdict: str,
        score: int,
        reasons: tuple[str, ...],
        quorum: int,
    ) -> PolicyDecision:
        return PolicyDecision(
            node_id=node_id,
            node_type=node_type,
            verdict=verdict,
            score=min(100, max(0, int(score))),
            reasons=tuple(reasons),
            quorum=quorum,
            policy_version=self.policy_version,
        )
