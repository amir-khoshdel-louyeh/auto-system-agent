"""Vision stub workers: explicit not-implemented verdicts (MVP).

`gui_click`/`gui_type` need the OS-tree/VLM grounding loop (C7a). They
consume their `steps:<type>` tasks and answer `step.failed` with a precise
reason instead of silently dropping work — fail-closed and auditable.

The `custom` node type is no longer a stub: it runs registered plugins in
the Phase 8 sandbox (`bus.workers.custom`).
"""

from __future__ import annotations

from typing import Any

from ..backend import Backend
from ..consumer import Runner
from .core import make_step_worker

STUB_REASONS = {
    "gui_click": "vision-worker stub: OS-tree/VLM grounding arrives phase 6 (C7a)",
    "gui_type": "vision-worker stub: OS-tree/VLM grounding arrives phase 6 (C7a)",
}


def make_stub_worker(backend: Backend, group: str, node_type: str) -> Runner:
    """Runner that fails every task of one future worker type, loudly."""

    async def refuse(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(STUB_REASONS.get(node_type, f"{node_type}-worker stub: not implemented"))

    return make_step_worker(backend, group, node_type, refuse)
