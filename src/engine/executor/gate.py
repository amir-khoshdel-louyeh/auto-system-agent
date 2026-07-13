"""Policy gate: own `command_guard` verdicts as the pre-node gate (C, no copy).

Maps `safety.command_guard.assess_command` (`ALLOW` / `CONFIRM` / `DENY`)
to the scheduler gate surface (`ALLOW` / `NEED_APPROVAL` / `DENY`);
`CONFIRM` is exactly today's `NEED_APPROVAL`. A light anomaly heuristic
escalates an `ALLOW` to `NEED_APPROVAL` when a shell command carries a
long obfuscated blob (placeholder for the phase-3 learned baseline).

Per node type:
- shell: assess `run` + anomaly check.
- approval: always `ALLOW` (the human quorum itself lives in the phase 6
  approval worker; the scheduler must not block it).
- gui_click / gui_type / custom: `NEED_APPROVAL` — they mutate OS state or
  run SDK code, so they stay human-gated until the sandbox + approver
  arrive (phase 6). Fail-closed by design.
"""

from __future__ import annotations

import re
from typing import Any

from safety.command_guard import assess_command

ALLOW = "ALLOW"
NEED_APPROVAL = "NEED_APPROVAL"
DENY = "DENY"

#: A single alphanumeric/blob token this long smells obfuscated (base64 payloads).
_BLOB_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")

_GATED_TYPES = ("gui_click", "gui_type", "custom")


def looks_obfuscated(command: str) -> bool:
    """Light anomaly: True when the command embeds a long encoded blob."""
    return bool(_BLOB_RE.search(command or ""))


def decide_for_command(command: str) -> tuple[str, str]:
    """Gate one shell command; returns (verdict, reason)."""
    assessment = assess_command(command)
    verdict = assessment.get("verdict", "DENY")
    reasons = assessment.get("reasons", [])
    reason = "; ".join(reasons) if reasons else verdict
    if verdict == "DENY":
        return (DENY, reason)
    if verdict == "CONFIRM":
        return (NEED_APPROVAL, reason)
    if looks_obfuscated(command):
        return (NEED_APPROVAL, "anomaly: long encoded blob in command")
    return (ALLOW, reason)


async def decide(node_id: str, spec: dict[str, Any]) -> str:
    """Scheduler gate entry: verdict for one node spec (reason kept in audit)."""
    node_type = spec.get("type", "")
    if node_type == "shell":
        verdict, _ = decide_for_command(str(spec.get("run", "")))
        return verdict
    if node_type == "approval":
        return ALLOW
    if node_type in _GATED_TYPES:
        return NEED_APPROVAL
    return DENY
