"""Frozen C9 MVP grammar (proposal §6 C9, plan.tmp phase 1).

- 5 node types: `shell`, `gui_click`, `gui_type`, `approval`, `custom`.
- 3 trigger kinds: `cron`, `webhook`, `event: file.arrived`.
- New node types are out-of-scope (scope guard S2–S3).

Per-type required params (validator enforces):
- shell: `run` (e.g. `setup.sh`)
- gui_click: `target` (e.g. `"Settings > Appearance > Dark"`)
- gui_type: `target` + `text`
- approval: `quorum` (positive int)
- custom: `func` (SDK symbol `def run(ctx) -> dict`)
"""

from __future__ import annotations

NODE_TYPES: tuple[str, ...] = ("shell", "gui_click", "gui_type", "approval", "custom")

TRIGGER_KEYS: tuple[str, ...] = ("cron", "webhook", "event")

ALLOWED_EVENTS: tuple[str, ...] = ("file.arrived",)

REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "shell": ("run",),
    "gui_click": ("target",),
    "gui_type": ("target", "text"),
    "approval": ("quorum",),
    "custom": ("func",),
}
