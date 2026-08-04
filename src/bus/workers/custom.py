"""Phase 8 item [76]: the `custom` worker runs plugins sandbox-first (proposal §10.8).

A `custom` node names a registered plugin (`func`) and its inputs; the worker
checks the declaration, runs the plugin in the sandbox — never in the worker
process — and publishes the sandbox verdict as the step output. A plugin that
is unknown, mis-declared or escapes its workspace fails the step with the
sandbox's reason, so a failed sandbox run can never look like success.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..backend import Backend
from ..consumer import Runner
from .core import (
    PersistStep,  # noqa: F401 - re-exported for callers
    make_step_worker,
)


def plugin_call(node: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Split a compiled custom node into ``(plugin name, inputs)``."""
    params = node.get("params", {}) if isinstance(node.get("params"), dict) else {}
    name = str(params.get("func") or "").strip()
    inputs = {key: value for key, value in params.items() if key != "func"}
    return name, inputs


def make_custom_worker(
    backend: Backend,
    group: str,
    registry=None,
    *,
    max_retries: int = 2,
    persist: PersistStep | None = None,
) -> Runner:
    """Runner over `steps:custom` backed by the sandboxed plugin registry."""
    from sandbox.plugins import PluginError, default_registry

    active = registry if registry is not None else default_registry()

    async def execute(node_id: str, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        name, inputs = plugin_call(node)
        if not name:
            raise RuntimeError(f"custom node '{node_id}' declares no plugin (func)")
        try:
            request = active.request(name, inputs)
            result = await asyncio.to_thread(active.run, name, inputs)
        except PluginError as exc:
            raise RuntimeError(f"custom node '{node_id}': {exc}") from exc
        if not result.ok:
            raise RuntimeError(f"plugin {name!r} failed in the sandbox: {result.output_summary}")
        return {
            "plugin": name,
            "sandbox": result.as_dict(),
            "isolation": result.isolation,
            "files": [change.path for change in result.files],
            "request_kind": request.kind,
        }

    return make_step_worker(
        backend, group, "custom", execute, max_retries=max_retries, persist=persist
    )
