"""Phase 8 item [76]: the plugin registry — custom nodes run sandbox-first.

The registry is the only source of truth about which plugin may run. A
workflow node's ``func`` names a registered plugin; its params become the
plugin's context after being checked against the manifest's declared inputs.
Nothing is imported into the host process: the source is seeded into a fresh
workspace and executed by :mod:`sandbox.runner`, so a plugin cannot act before
it has been isolated, and a plugin that is not registered is not run at all.

Discovery is convention-based and explicit: a ``<name>.json`` manifest in the
plugin directory declares ``<name>.py`` next to it. There is no import-time
scanning of the process, and no plugin becomes available by being on
``sys.path``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import SandboxRequest, SandboxResult
from .manifest import ManifestError, PluginManifest, load_manifest
from .runner import run

#: Where discovery looks when the caller does not say (tests pass their own).
DEFAULT_PLUGIN_DIR = Path(os.getenv("AUTO_AGENT_PLUGIN_DIR", "plugins"))

#: Extra files per plugin are bounded like seed files.
MAX_EXTRA_FILES = 8


class PluginError(RuntimeError):
    """A plugin is unknown, malformed or not runnable."""


@dataclass(frozen=True)
class RegisteredPlugin:
    """A validated manifest plus the source it declares."""

    manifest: PluginManifest
    source: str
    extra_files: dict[str, str]

    def files(self) -> dict[str, str]:
        """Seed files for one sandbox run (entrypoint plus helpers)."""
        return {self.manifest.entrypoint: self.source, **self.extra_files}


class PluginRegistry:
    """Registry of sandbox-approved plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, RegisteredPlugin] = {}

    # -- registration -----------------------------------------------------
    def register(
        self,
        manifest: PluginManifest | dict[str, Any],
        source: str,
        *,
        extra_files: dict[str, str] | None = None,
        replace: bool = False,
    ) -> PluginManifest:
        """Register a plugin; refuses unknown schemas and duplicate names."""
        parsed = load_manifest(manifest) if isinstance(manifest, dict) else manifest.require_valid()
        if parsed.name in self._plugins and not replace:
            raise PluginError(f"plugin {parsed.name!r} is already registered")
        if not isinstance(source, str) or not source.strip():
            raise PluginError(f"plugin {parsed.name!r} has no source")
        extras = dict(extra_files or {})
        if len(extras) > MAX_EXTRA_FILES:
            raise PluginError(f"plugin {parsed.name!r} declares too many helper files")
        for path in extras:
            if path.startswith(("/", "~")) or ".." in path.split("/"):
                raise PluginError(f"helper file {path!r} escapes the plugin directory")
            if path == parsed.entrypoint:
                raise PluginError(f"helper file {path!r} collides with the entrypoint")
        self._plugins[parsed.name] = RegisteredPlugin(
            manifest=parsed, source=source, extra_files=extras
        )
        return parsed

    def discover(self, directory: str | Path | None = None) -> list[PluginManifest]:
        """Register every ``<name>.json`` + ``<name>.py`` pair in a directory."""
        folder = Path(directory) if directory is not None else DEFAULT_PLUGIN_DIR
        if not folder.is_dir():
            return []
        registered: list[PluginManifest] = []
        for manifest_path in sorted(folder.glob("*.json")):
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = load_manifest(raw)
            except (OSError, ValueError, ManifestError) as exc:
                raise PluginError(f"invalid plugin manifest {manifest_path}: {exc}") from exc
            source_path = manifest_path.with_suffix(".py")
            if not source_path.is_file():
                raise PluginError(f"plugin {manifest.name!r} has no source at {source_path}")
            registered.append(self.register(manifest, source_path.read_text(encoding="utf-8")))
        return registered

    # -- introspection ----------------------------------------------------
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))

    def get(self, name: str) -> RegisteredPlugin:
        try:
            return self._plugins[name]
        except KeyError:
            raise PluginError(
                f"plugin {name!r} is not registered (known: {list(self.names())})"
            ) from None

    def describe(self) -> list[dict[str, Any]]:
        return [self._plugins[name].manifest.as_dict() for name in self.names()]

    # -- execution --------------------------------------------------------
    def request(self, name: str, params: dict[str, Any] | None = None) -> SandboxRequest:
        """Build the sandbox request for one call (validates declared inputs)."""
        plugin = self.get(name)
        context = dict(params or {})
        problems = plugin.manifest.input_problems(context)
        if problems:
            raise PluginError(f"plugin {name!r}: " + "; ".join(problems))
        return SandboxRequest(
            kind="plugin",
            module=plugin.manifest.entrypoint,
            files=plugin.files(),
            params=context,
            timeout_s=plugin.manifest.timeout_s,
        )

    def run(self, name: str, params: dict[str, Any] | None = None) -> SandboxResult:
        """Run one registered plugin inside the sandbox."""
        return run(self.request(name, params))


def default_registry() -> PluginRegistry:
    """A registry discovered from ``AUTO_AGENT_PLUGIN_DIR`` (empty if absent)."""
    registry = PluginRegistry()
    registry.discover(DEFAULT_PLUGIN_DIR)
    return registry
