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

import os
import re
from dataclasses import dataclass, field
from typing import Any

from safety.command_guard import (
    CommandSyntaxError,
    assess_command,
    iter_command_nodes,
    parse_command,
)

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


#: Roles that may execute nodes by default (RBAC, item [34]).
ALLOWED_EXECUTOR_ROLES: tuple[str, ...] = ("OWNER", "OPERATOR")


@dataclass(frozen=True)
class PolicyContext:
    """Caller-supplied facts the decision needs (item [34]).

    - ``role``: effective role of the caller (workspace-scoped);
    - ``allowed_roles``: roles permitted to execute nodes;
    - ``workspace_root``: approved directory a shell node may touch.
    """

    role: str = "OPERATOR"
    allowed_roles: tuple[str, ...] = field(default_factory=lambda: ALLOWED_EXECUTOR_ROLES)
    workspace_root: str = ""


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

    def decide(
        self,
        node_id: str,
        spec: dict[str, Any],
        *,
        context: PolicyContext | None = None,
    ) -> PolicyDecision:
        """Return the frozen decision for one node spec.

        With a :class:`PolicyContext`, a caller whose role may not execute
        nodes is denied, and a shell node that references a path outside the
        approved workspace is denied (fail closed, item [34]).
        """
        node_type = str(spec.get("type", ""))
        if context is not None and context.allowed_roles and context.role not in context.allowed_roles:
            return self._make(
                node_id,
                node_type or "?",
                DENY,
                100,
                (f"role '{context.role}' may not execute nodes",),
                0,
            )
        if node_type in _COMMAND_TYPES:
            decision = self._decide_command(node_id, node_type, str(spec.get("run", "")))
            if context is not None and context.workspace_root:
                violations = _path_violations(str(spec.get("run", "")), context.workspace_root)
                if violations:
                    return self._make(
                        node_id,
                        node_type,
                        DENY,
                        max(decision.score, MEDIUM_MAX + 1),
                        violations,
                        0,
                    )
            return decision
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


def _path_like(arg: str) -> bool:
    """True when an argv token names a path rather than a plain value."""
    return arg.startswith(("/", "~", "./", "../")) or "/" in arg


def _path_violations(command: str, workspace_root: str) -> tuple[str, ...]:
    """Reasons a command violates the workspace boundary (item [34]).

    `..` traversal and absolute/`~` paths that resolve outside the approved
    workspace root are reported; relative paths inside the workspace pass.
    """
    if not workspace_root:
        return ()
    try:
        node = parse_command(command)
    except CommandSyntaxError:
        return ()
    root = os.path.abspath(os.path.expanduser(workspace_root))
    reasons: list[str] = []
    for leaf in iter_command_nodes(node):
        for arg in leaf.argv[1:]:
            if not arg or arg.startswith("-"):
                continue
            cleaned = arg.strip().strip("'\"")
            if not cleaned or not _path_like(cleaned):
                continue
            if ".." in cleaned.split("/"):
                reasons.append(f"path traversal in '{cleaned}'")
                continue
            if cleaned.startswith(("/", "~")):
                target = os.path.abspath(os.path.expanduser(cleaned))
                if target != root and not target.startswith(root + os.sep):
                    reasons.append(f"path '{cleaned}' is outside the workspace")
    return tuple(reasons)
