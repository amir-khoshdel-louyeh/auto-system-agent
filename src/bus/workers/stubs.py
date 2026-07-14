"""Vision/custom stub workers: explicit not-implemented verdicts (MVP).

`gui_click`/`gui_type` need the OS-tree/VLM grounding loop (phase 6, C7a)
and `custom` needs the sandboxed Plugin SDK (phase 6/7, C8/C9). Until then
these stubs consume their `steps:<type>` tasks and answer `step.failed`
with a precise reason instead of silently dropping work — fail-closed and
auditable.
"""

from __future__ import annotations

from typing import Any

from ..backend import Backend
from ..consumer import Runner
from .core import make_step_worker

STUB_REASONS = {
    "gui_click": "vision-worker stub: OS-tree/VLM grounding arrives phase 6 (C7a)",
    "gui_type": "vision-worker stub: OS-tree/VLM grounding arrives phase 6 (C7a)",
    "custom": "custom-worker stub: sandboxed Plugin SDK arrives phase 6/7 (C8/C9)",
}


def make_stub_worker(backend: Backend, group: str, node_type: str) -> Runner:
    """Runner that fails every task of one future worker type, loudly."""

    async def refuse(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(STUB_REASONS.get(node_type, f"{node_type}-worker stub: not implemented"))

    return make_step_worker(backend, group, node_type, refuse)
