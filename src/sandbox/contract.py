"""Phase 8 item [73]: the frozen sandbox request/response contract.

A sandbox run is one request in, one result out, with no ambient state: the
caller seeds a workspace, names either a Python module (`kind="plugin"`) or
an argv (`kind="command"`), and gets back a normalized verdict plus file
*diff metadata* — never raw file contents and never host paths.

The contract is versioned (`sandbox-v1`) and validated **before** anything is
executed, so a malformed request is a caller error rather than a sandbox
escape attempt: paths must be workspace-relative, timeouts are bounded, and
an unknown kind is refused.

Frozen result shape::

    {"contract_version", "ok", "exit_code", "timed_out", "duration_ms",
     "stdout", "stderr", "output_summary", "result", "files", "denied",
     "violations", "isolation"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal  # noqa: F401 - Literal documents the kind field

CONTRACT_VERSION = "sandbox-v1"

#: Kinds of payload a sandbox run can execute.
KINDS: tuple[str, ...] = ("plugin", "command")

#: Hard bounds — the caller may ask for less, never more.
MIN_TIMEOUT_S = 0.05
MAX_TIMEOUT_S = 30.0
DEFAULT_TIMEOUT_S = 5.0
MAX_OUTPUT_BYTES = 64_000
MAX_SEED_FILES = 32
MAX_SEED_BYTES = 256_000

#: Actions reported in a file diff.
FILE_ACTIONS: tuple[str, ...] = ("created", "modified", "deleted")


class SandboxError(RuntimeError):
    """The sandbox refused a request before running anything."""


@dataclass(frozen=True)
class SandboxRequest:
    """One unit of untrusted work (item [73])."""

    kind: Literal["plugin", "command"] = "plugin"
    module: str = ""
    command: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    stdin: str = ""
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_output_bytes: int = MAX_OUTPUT_BYTES
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> tuple[str, ...]:
        """Return every reason the request is invalid (empty means valid)."""
        problems: list[str] = []
        if self.contract_version != CONTRACT_VERSION:
            problems.append(
                f"unsupported contract_version {self.contract_version!r}, "
                f"expected {CONTRACT_VERSION!r}"
            )
        if self.kind not in KINDS:
            problems.append(f"unknown kind {self.kind!r}, expected one of {list(KINDS)}")
        if self.kind == "plugin":
            if not self.module:
                problems.append("plugin request needs a module")
            elif not _safe_relative(self.module):
                problems.append(f"module {self.module!r} must be a relative workspace path")
            elif not self.module.endswith(".py"):
                problems.append(f"module {self.module!r} must be a .py file")
            if self.command:
                problems.append("plugin request must not also carry a command")
        if self.kind == "command":
            if not self.command:
                problems.append("command request needs a non-empty argv")
            if self.module:
                problems.append("command request must not also carry a module")
        for path in self.files:
            if not _safe_relative(path):
                problems.append(f"seed file {path!r} must be a relative workspace path")
        total_bytes = sum(len(text.encode("utf-8")) for text in self.files.values())
        if total_bytes > MAX_SEED_BYTES:
            problems.append(f"seed files exceed {MAX_SEED_BYTES} bytes")
        if len(self.files) > MAX_SEED_FILES:
            problems.append(f"at most {MAX_SEED_FILES} seed files are allowed")
        if not isinstance(self.timeout_s, (int, float)) or not (
            MIN_TIMEOUT_S <= float(self.timeout_s) <= MAX_TIMEOUT_S
        ):
            problems.append(
                f"timeout_s must be between {MIN_TIMEOUT_S} and {MAX_TIMEOUT_S} seconds"
            )
        if not isinstance(self.max_output_bytes, int) or not (
            1 <= self.max_output_bytes <= MAX_OUTPUT_BYTES
        ):
            problems.append(f"max_output_bytes must be between 1 and {MAX_OUTPUT_BYTES}")
        return tuple(problems)

    def require_valid(self) -> None:
        """Raise :class:`SandboxError` when the request is malformed."""
        problems = self.validate()
        if problems:
            raise SandboxError("; ".join(problems))

    def payload(self) -> dict[str, Any]:
        """The JSON document the child harness receives."""
        return {
            "contract_version": self.contract_version,
            "kind": self.kind,
            "module": self.module,
            "command": list(self.command),
            "params": dict(self.params),
            "stdin": self.stdin,
            "max_output_bytes": int(self.max_output_bytes),
        }


@dataclass(frozen=True)
class FileChange:
    """Diff metadata for one file (path is workspace-relative)."""

    path: str
    action: str
    size: int = 0
    sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SandboxResult:
    """Normalized output of one sandbox run (item [73])."""

    ok: bool = False
    exit_code: int = -1
    timed_out: bool = False
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    output_summary: str = ""
    result: dict[str, Any] | None = None
    files: tuple[FileChange, ...] = ()
    denied: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    #: Which layer enforced the run:
    #: - ``bwrap`` — OS namespaces, with a private ``/proc``;
    #: - ``bwrap-no-proc`` — OS namespaces on a host that refuses the fresh
    #:   ``/proc`` mount (confinement is the same, tools reading ``/proc``
    #:   fail);
    #: - ``audit`` — in-Python audit hook only (plugins; no command execution).
    isolation: str = "audit"
    contract_version: str = CONTRACT_VERSION

    @property
    def writes(self) -> tuple[FileChange, ...]:
        """Every file the run created, modified or deleted."""
        return tuple(change for change in self.files if change.action in FILE_ACTIONS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_summary": self.output_summary,
            "result": self.result,
            "files": [change.as_dict() for change in self.files],
            "denied": list(self.denied),
            "violations": list(self.violations),
            "isolation": self.isolation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxResult:
        """Rebuild a result from its JSON form (API/client boundary)."""
        files = tuple(
            FileChange(
                path=str(entry.get("path", "")),
                action=str(entry.get("action", "created")),
                size=int(entry.get("size", 0)),
                sha256=str(entry.get("sha256", "")),
            )
            for entry in data.get("files", [])
        )
        return cls(
            ok=bool(data.get("ok", False)),
            exit_code=int(data.get("exit_code", -1)),
            timed_out=bool(data.get("timed_out", False)),
            duration_ms=int(data.get("duration_ms", 0)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            output_summary=str(data.get("output_summary", "")),
            result=data.get("result"),
            files=files,
            denied=tuple(str(item) for item in data.get("denied", [])),
            violations=tuple(str(item) for item in data.get("violations", [])),
            isolation=str(data.get("isolation", "audit")),
            contract_version=str(data.get("contract_version", CONTRACT_VERSION)),
        )


def is_safe_relative(path: str) -> bool:
    """True for a non-empty relative path that cannot climb out of the root.

    Public because callers outside the contract (a node's ``workdir``, a
    plugin's helper files) must apply the same rule instead of re-inventing
    it — a second, laxer copy is how traversal gets in.
    """
    return _safe_relative(path)


def _safe_relative(path: str) -> bool:
    """True for a non-empty relative path that cannot climb out of the root."""
    text = str(path or "").replace("\\", "/")
    if not text or text.startswith("/") or text.startswith("~"):
        return False
    if ":" in text.split("/")[0]:  # Windows drive ('C:')
        return False
    parts = [part for part in text.split("/") if part not in ("", ".")]
    return bool(parts) and ".." not in parts
