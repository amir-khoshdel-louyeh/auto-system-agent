"""Phase 8 items [73]/[74]: run untrusted code in a disposable sandbox.

Two layers, defence in depth:

1. **OS namespaces** via ``bwrap`` when the host allows unprivileged user
   namespaces: the workspace is the only writable bind mount, the network,
   PID, IPC and UTS namespaces are unshared, and the interpreter sees a
   read-only system tree. This is the layer the Phase 8 gate relies on.
2. **In-Python audit hook** (`sandbox.harness`): even without ``bwrap``, a
   plugin cannot write outside the workspace, spawn a process or open a
   socket — every attempt is denied and recorded in the result.

The parent enforces the wall-clock timeout (killing the whole process
group), applies ``RLIMIT_CPU``/``AS``/``FSIZE``/``NOFILE``/``NPROC`` before
exec, hashes the workspace before and after to report *file diff metadata*,
and watches a sentinel directory outside the workspace: any change there is
an isolation violation, and any violation makes the result ``ok=False``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from .contract import (
    CONTRACT_VERSION,
    MAX_OUTPUT_BYTES,
    FileChange,
    SandboxError,
    SandboxRequest,
    SandboxResult,
)

HARNESS = Path(__file__).resolve().with_name("harness.py")

#: Files larger than this are killed by ``RLIMIT_FSIZE`` (1 MiB).
MAX_FILE_BYTES = 1_000_000
#: Address-space cap for the child (512 MiB).
MAX_ADDRESS_SPACE = 512 * 1024 * 1024

BWRAP_PROBE_WORKSPACE = "/tmp"


@lru_cache(maxsize=1)
def bwrap_available() -> bool:
    """True when ``bwrap`` can actually create the namespaces we need."""
    executable = shutil.which("bwrap")
    if not executable:
        return False
    try:
        probe = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                executable,
                "--unshare-all",
                "--die-with-parent",
                "--ro-bind",
                "/",
                "/",
                "--",
                sys.executable,
                "-I",
                "-c",
                "pass",
            ],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _limits() -> None:  # pragma: no cover - runs in the forked child
    """Apply kernel-enforced resource limits just before exec."""
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    pairs = (
        (resource.RLIMIT_CPU, (8, 8)),
        (resource.RLIMIT_AS, (MAX_ADDRESS_SPACE, MAX_ADDRESS_SPACE)),
        (resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES)),
        (resource.RLIMIT_NOFILE, (64, 64)),
        (resource.RLIMIT_CORE, (0, 0)),
    )
    for which, value in pairs:
        try:
            resource.setrlimit(which, value)
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            continue
    # Deliberately no RLIMIT_NPROC: it is per-user, so a low value makes
    # bwrap's own namespace clone fail with EAGAIN. Process creation is
    # blocked by the audit hook and the unshared PID namespace instead.


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """Size + sha256 of every regular file under ``root`` (relative paths)."""
    found: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        found[str(path.relative_to(root))] = (path.stat().st_size, digest)
    return found


def _diff(
    before: dict[str, tuple[int, str]], after: dict[str, tuple[int, str]]
) -> tuple[FileChange, ...]:
    """Normalized created/modified/deleted metadata between two snapshots."""
    changes: list[FileChange] = []
    for path in sorted(after):
        size, digest = after[path]
        if path not in before:
            changes.append(FileChange(path=path, action="created", size=size, sha256=digest))
        elif before[path] != (size, digest):
            changes.append(FileChange(path=path, action="modified", size=size, sha256=digest))
    for path in sorted(set(before) - set(after)):
        changes.append(FileChange(path=path, action="deleted"))
    return tuple(changes)


def _seed(workspace: Path, files: dict[str, str]) -> None:
    """Write the caller's seed files into the workspace."""
    for relative, content in files.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _inner_command(request: SandboxRequest, result_path: str) -> list[str]:
    """The argv executed inside the sandbox."""
    if request.kind == "plugin":
        return [
            sys.executable,
            "-I",
            "-B",
            result_path.rsplit("/", 1)[0] + "/harness.py",
            result_path,
        ]
    return list(request.command)


def _bwrap_argv(
    request: SandboxRequest, workspace: Path, out_dir: Path, result_path: str
) -> list[str]:
    """bwrap argv: only the workspace and the result dir are writable."""
    executable = shutil.which("bwrap") or "bwrap"
    argv = [executable, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    # Only the runtime needs to be visible: a plugin cannot read host secrets
    # such as /etc/passwd or $HOME because those paths are not bound at all.
    for system in ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64"):
        if Path(system).exists():
            argv += ["--ro-bind", system, system]
    for cache in ("/etc/ld.so.cache", "/etc/alternatives", "/etc/localtime"):
        if Path(cache).exists():
            argv += ["--ro-bind", cache, cache]
    argv += [
        "--bind",
        str(workspace),
        "/work",
        "--bind",
        str(out_dir),
        "/out",
        "--ro-bind",
        str(HARNESS.parent),
        "/harness",
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        "/work",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/work/home",
        "--setenv",
        "TMPDIR",
        "/work/tmp",
        "--",
    ]
    if request.kind == "plugin":
        argv += [sys.executable, "-I", "-B", "/harness/harness.py", result_path]
    else:
        argv += list(request.command)
    return argv


def _plain_argv(request: SandboxRequest, result_path: str) -> list[str]:
    """Fallback argv without bwrap (audit-hook guarded plugin execution)."""
    if request.kind != "plugin":
        raise SandboxError(
            "command execution needs OS isolation (bwrap with user namespaces); "
            "the audit-hook sandbox only runs Python plugins"
        )
    return [sys.executable, "-I", "-B", str(HARNESS), result_path]


def run(
    request: SandboxRequest,
    *,
    root: str | Path | None = None,
    keep_root: bool = False,
) -> SandboxResult:
    """Execute one request and return the normalized result (item [73]).

    ``root`` lets a caller own the disposable tree (tests); it must exist and
    is populated with ``workspace/`` and ``outside/``. By default a temporary
    directory is used and removed afterwards.
    """
    request.require_valid()
    isolation = "bwrap" if bwrap_available() else "audit"
    base = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix="sandbox-"))
    workspace = base / "workspace"
    outside = base / "outside"
    out_dir = base / ".out"
    for directory in (workspace, outside, out_dir, workspace / "home", workspace / "tmp"):
        directory.mkdir(parents=True, exist_ok=True)
    result_path_host = out_dir / "result.json"
    _seed(workspace, request.files)

    before_workspace = _snapshot(workspace)
    before_outside = _snapshot(outside)
    payload = json.dumps(request.payload()).encode("utf-8")

    try:
        argv = (
            _bwrap_argv(request, workspace, out_dir, "/out/result.json")
            if isolation == "bwrap"
            else _plain_argv(request, str(result_path_host))
        )
    except SandboxError as exc:
        if not keep_root:
            shutil.rmtree(base, ignore_errors=True)
        return SandboxResult(
            ok=False,
            exit_code=126,
            stderr=str(exc),
            output_summary=f"refused: {exc}",
            isolation=isolation,
        )

    started = time.monotonic()
    timed_out = False
    stdout = stderr = b""
    exit_code = -1
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=str(workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_limits if os.name == "posix" else None,
        )
        try:
            stdout, stderr = process.communicate(payload, timeout=request.timeout_s)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process)
            stdout, stderr = process.communicate()
            exit_code = -9
    except OSError as exc:  # pragma: no cover - spawn failure
        stderr = str(exc).encode("utf-8")
        exit_code = 127
    duration_ms = int((time.monotonic() - started) * 1000)

    after_workspace = _snapshot(workspace)
    after_outside = _snapshot(outside)
    violations = tuple(
        f"outside workspace changed: {change.path} ({change.action})"
        for change in _diff(before_outside, after_outside)
    )

    document = _read_result(result_path_host)
    denied = tuple(str(item) for item in document.get("denied", [])) if document else ()
    result_value = document.get("result") if document else None
    child_out = str(document.get("stdout", "")) if document else _decode(stdout)
    child_err = str(document.get("stderr", "")) if document else _decode(stderr)
    if document is None and request.kind == "plugin":
        child_err = (child_err + "\n" + _decode(stderr)).strip()

    effective_exit = int(document.get("exit_code", exit_code)) if document else exit_code
    ok = effective_exit == 0 and not timed_out and not violations and not denied
    summary = _summary(
        request=request,
        ok=ok,
        timed_out=timed_out,
        exit_code=effective_exit,
        document=document,
        child_err=child_err,
        violations=violations,
        denied=denied,
    )
    result = SandboxResult(
        ok=ok,
        exit_code=effective_exit,
        timed_out=timed_out,
        duration_ms=duration_ms,
        stdout=child_out[: request.max_output_bytes],
        stderr=child_err[: request.max_output_bytes],
        output_summary=summary,
        result=result_value if isinstance(result_value, dict) else None,
        files=_diff(before_workspace, after_workspace),
        denied=denied,
        violations=violations,
        isolation=isolation,
        contract_version=CONTRACT_VERSION,
    )
    if not keep_root:
        shutil.rmtree(base, ignore_errors=True)
    return result


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the whole process group (bwrap children included)."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover
        process.kill()


def _read_result(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace") if raw else ""


def _summary(
    *,
    request: SandboxRequest,
    ok: bool,
    timed_out: bool,
    exit_code: int,
    document: dict,
    child_err: str,
    violations: tuple[str, ...],
    denied: tuple[str, ...],
) -> str:
    """One-line normalized verdict (the only free-text the UI shows)."""
    if timed_out:
        return f"timed out after {request.timeout_s}s"
    if violations:
        return f"isolation violation: {violations[0]}"
    if denied:
        return f"denied: {denied[0]}"
    if ok:
        return "ok"
    error = str(document.get("error", "")).strip() if document else ""
    detail = error or child_err.strip().splitlines()[-1] if child_err.strip() else ""
    detail = detail[:200]
    return f"failed (exit {exit_code})" + (f": {detail}" if detail else "")


__all__ = [
    "BWRAP_PROBE_WORKSPACE",
    "HARNESS",
    "MAX_ADDRESS_SPACE",
    "MAX_FILE_BYTES",
    "MAX_OUTPUT_BYTES",
    "bwrap_available",
    "run",
]
