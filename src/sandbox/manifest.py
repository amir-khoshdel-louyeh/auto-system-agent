"""Phase 8 item [75]: plugin manifest validation (proposal §10.8).

A plugin is registered by declaration, not by trust: the manifest names the
entrypoint, the typed inputs/outputs it exchanges and the capabilities it
asks for. The schema is **closed** — an unknown key is an error rather than
something silently ignored — so a typo cannot quietly widen what a plugin is
allowed to do.

What a manifest may never ask for: network access. The sandbox has no
network, so a manifest that declares ``network: true`` is refused instead of
being registered with an unenforceable promise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .contract import DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, MIN_TIMEOUT_S

MANIFEST_VERSION = "plugin-v1"

#: Value types a declared input/output may use (frozen vocabulary).
FIELD_TYPES: tuple[str, ...] = ("string", "int", "float", "bool", "path", "json")

#: Capabilities a plugin may declare. The sandbox only ever provides these.
CAPABILITIES: tuple[str, ...] = ("fs:workspace",)

REQUIRED_KEYS: tuple[str, ...] = ("name", "version", "entrypoint")
OPTIONAL_KEYS: tuple[str, ...] = (
    "description",
    "inputs",
    "outputs",
    "capabilities",
    "timeout_s",
    "network",
    "manifest_version",
)
ALLOWED_KEYS: tuple[str, ...] = REQUIRED_KEYS + OPTIONAL_KEYS

MAX_FIELDS = 32
MAX_DESCRIPTION = 500

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ManifestError(ValueError):
    """A plugin manifest is not registrable."""


@dataclass(frozen=True)
class PluginManifest:
    """One plugin declaration (frozen once validated)."""

    name: str
    version: str
    entrypoint: str
    description: str = ""
    inputs: Mapping[str, str] = field(default_factory=dict)
    outputs: Mapping[str, str] = field(default_factory=dict)
    capabilities: tuple[str, ...] = ("fs:workspace",)
    timeout_s: float = DEFAULT_TIMEOUT_S
    network: bool = False
    manifest_version: str = MANIFEST_VERSION

    def validate(self) -> tuple[str, ...]:
        """Every reason this manifest is not registrable (empty = valid)."""
        problems: list[str] = []
        if self.manifest_version != MANIFEST_VERSION:
            problems.append(
                f"unsupported manifest_version {self.manifest_version!r}, "
                f"expected {MANIFEST_VERSION!r}"
            )
        if not _NAME_RE.match(self.name or ""):
            problems.append("name must match [a-z][a-z0-9_-]{2,63} (lowercase, 3-64 characters)")
        if not _VERSION_RE.match(self.version or ""):
            problems.append("version must be MAJOR.MINOR.PATCH (e.g. 1.0.0)")
        if not self.entrypoint or not self.entrypoint.endswith(".py"):
            problems.append("entrypoint must be a workspace-relative .py file")
        elif self.entrypoint.startswith(("/", "~")) or ".." in self.entrypoint.split("/"):
            problems.append(f"entrypoint {self.entrypoint!r} escapes the plugin directory")
        problems.extend(_field_problems("inputs", self.inputs))
        problems.extend(_field_problems("outputs", self.outputs))
        for capability in self.capabilities:
            if capability not in CAPABILITIES:
                problems.append(
                    f"capability {capability!r} is not provided by the sandbox "
                    f"(allowed: {list(CAPABILITIES)})"
                )
        if self.network:
            problems.append("network access cannot be declared: the sandbox has no network")
        if not isinstance(self.timeout_s, (int, float)) or not (
            MIN_TIMEOUT_S <= float(self.timeout_s) <= MAX_TIMEOUT_S
        ):
            problems.append(
                f"timeout_s must be between {MIN_TIMEOUT_S} and {MAX_TIMEOUT_S} seconds"
            )
        if len(self.description) > MAX_DESCRIPTION:
            problems.append(f"description exceeds {MAX_DESCRIPTION} characters")
        return tuple(problems)

    def require_valid(self) -> PluginManifest:
        """Raise :class:`ManifestError` unless the manifest is registrable."""
        problems = self.validate()
        if problems:
            raise ManifestError("; ".join(problems))
        return self

    def input_problems(self, params: Mapping[str, Any]) -> tuple[str, ...]:
        """Check one call's params against the declared inputs."""
        problems = [f"missing input {name!r}" for name in self.inputs if name not in params]
        for name, kind in self.inputs.items():
            if name in params and not _matches(kind, params[name]):
                problems.append(f"input {name!r} must be {kind}")
        for name in params:
            if name not in self.inputs:
                problems.append(f"undeclared input {name!r}")
        return tuple(problems)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "name": self.name,
            "version": self.version,
            "entrypoint": self.entrypoint,
            "description": self.description,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "capabilities": list(self.capabilities),
            "timeout_s": self.timeout_s,
            "network": self.network,
        }


def _field_problems(kind: str, fields: Mapping[str, str]) -> list[str]:
    """Validate one declared input/output map."""
    problems: list[str] = []
    if not isinstance(fields, Mapping):
        return [f"{kind} must be an object of name -> type"]
    if len(fields) > MAX_FIELDS:
        problems.append(f"{kind} declares more than {MAX_FIELDS} fields")
    for name, field_type in fields.items():
        if not isinstance(name, str) or not _FIELD_RE.match(name):
            problems.append(f"{kind} field {name!r} must be a lowercase identifier")
        if field_type not in FIELD_TYPES:
            problems.append(
                f"{kind} field {name!r} has unknown type {field_type!r} "
                f"(allowed: {list(FIELD_TYPES)})"
            )
    return problems


def _matches(kind: str, value: Any) -> bool:
    """Runtime check for one declared field type."""
    if kind == "string":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "path":
        return (
            isinstance(value, str)
            and bool(value)
            and not value.startswith(("/", "~"))
            and ".." not in value.replace("\\", "/").split("/")
        )
    if kind == "json":
        return isinstance(value, (dict, list, str, int, float, bool)) or value is None
    return False


def load_manifest(raw: Mapping[str, Any]) -> PluginManifest:
    """Build a manifest from raw JSON, rejecting unknown/missing keys."""
    if not isinstance(raw, Mapping):
        raise ManifestError("manifest must be a JSON object")
    unknown = sorted(set(raw) - set(ALLOWED_KEYS))
    if unknown:
        raise ManifestError(f"unknown manifest keys: {unknown}")
    missing = [key for key in REQUIRED_KEYS if not raw.get(key)]
    if missing:
        raise ManifestError(f"missing required manifest keys: {missing}")
    inputs = raw.get("inputs") or {}
    outputs = raw.get("outputs") or {}
    capabilities = raw.get("capabilities")
    if capabilities is None:
        capabilities = list(CAPABILITIES)
    if not isinstance(capabilities, (list, tuple)):
        raise ManifestError("capabilities must be a list")
    manifest = PluginManifest(
        name=str(raw["name"]),
        version=str(raw["version"]),
        entrypoint=str(raw["entrypoint"]),
        description=str(raw.get("description") or ""),
        inputs=dict(inputs) if isinstance(inputs, Mapping) else inputs,
        outputs=dict(outputs) if isinstance(outputs, Mapping) else outputs,
        capabilities=tuple(str(item) for item in capabilities),
        timeout_s=float(raw.get("timeout_s") or DEFAULT_TIMEOUT_S),
        network=bool(raw.get("network", False)),
        manifest_version=str(raw.get("manifest_version") or MANIFEST_VERSION),
    )
    return manifest.require_valid()


def load_manifest_file(path: str | Path) -> PluginManifest:
    """Read and validate one ``*.json`` manifest from disk."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    except ValueError as exc:
        raise ManifestError(f"manifest {path} is not valid JSON: {exc}") from exc
    return load_manifest(raw)
