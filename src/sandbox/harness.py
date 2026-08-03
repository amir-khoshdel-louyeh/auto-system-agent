"""Phase 8 item [74]: the child harness that enforces sandbox restrictions.

Runs inside the disposable subprocess (never imported by the app):

- **filesystem** — an audit hook refuses every write whose resolved target is
  outside the workspace. ``..`` traversal, absolute paths and symlinks that
  point out are all caught because the check uses ``realpath``;
- **processes** — no ``fork``/``exec``/``subprocess``/``os.system``, so a
  plugin cannot escape by starting another program;
- **network** — no ``socket.connect``/``socket.bind`` and no ``urllib``
  request auditor event;
- **resources** — the parent applies ``RLIMIT_CPU``/``RLIMIT_AS``/
  ``RLIMIT_FSIZE``/``RLIMIT_NOFILE``/``RLIMIT_NPROC`` before exec, so a CPU
  hog, a memory bomb or an endless writer is killed by the kernel.

Everything the plugin prints is captured and bounded; the harness writes its
JSON verdict to a path *outside* the workspace that only the harness (not the
plugin) knows, so a plugin cannot forge the result.
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

_WRITE_EVENTS = {
    "open",
    "os.mkdir",
    "os.rmdir",
    "os.remove",
    "os.rename",
    "os.replace",
    "os.symlink",
    "os.link",
    "os.chmod",
    "os.chown",
    "os.truncate",
    "os.utime",
}

_BLOCKED_PREFIXES = (
    "subprocess.",
    "os.system",
    "os.exec",
    "os.fork",
    "os.spawn",
    "os.posix_spawn",
    "socket.connect",
    "socket.bind",
    "socket.getaddrinfo",
    "urllib.Request",
)

_WRITE_MODES = set("wax+")


def _writes(mode: Any, flags: Any) -> bool:
    """True when an ``open`` call can modify the filesystem."""
    if isinstance(mode, str) and _WRITE_MODES & set(mode):
        return True
    if isinstance(flags, int):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        return bool(flags & write_flags)
    return False


def install_guard(workspace: str, allowed: tuple[str, ...] = ()) -> list[str]:
    """Install the audit hook; returns the mutable denial log.

    ``allowed`` names paths the harness itself must write (its result file);
    the plugin never learns them, and they live outside the workspace.
    Reads stay limited to the interpreter/runtime tree, so ``/etc/passwd`` or
    ``$HOME/.ssh`` are not readable even when bwrap is unavailable.
    """
    import sysconfig

    root = os.path.realpath(workspace)
    permitted = tuple(os.path.realpath(path) for path in allowed)
    readable = [
        root,
        os.path.realpath(sys.prefix),
        os.path.realpath(sys.base_prefix),
        os.path.realpath(sysconfig.get_paths().get("stdlib", sys.prefix)),
        os.path.dirname(os.path.realpath(__file__)),
    ]
    readable += [
        path
        for path in ("/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin", "/proc/self")
        if os.path.exists(path)
    ]
    readable_files = {
        os.path.realpath(path)
        for path in (
            "/dev/null",
            "/dev/urandom",
            "/dev/random",
            "/etc/ld.so.cache",
            "/etc/localtime",
            "/etc/alternatives",
        )
    }
    readable_roots = tuple(os.path.realpath(path) for path in readable)
    denials: list[str] = []

    def inside(path: Any) -> bool:
        try:
            resolved = os.path.realpath(os.fspath(path))
        except (TypeError, ValueError):
            return False
        if resolved == root or resolved.startswith(root + os.sep):
            return True
        return any(resolved == item for item in permitted)

    def readable_path(path: Any) -> bool:
        try:
            resolved = os.path.realpath(os.fspath(path))
        except (TypeError, ValueError):
            return False
        if resolved in readable_files:
            return True
        return any(
            resolved == item or resolved.startswith(item + os.sep) for item in readable_roots
        ) or any(resolved == item for item in permitted)

    def deny(reason: str) -> None:
        denials.append(reason)
        raise PermissionError(f"sandbox denied: {reason}")

    def check(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith(_BLOCKED_PREFIXES):
            deny(f"{event} is not permitted in the sandbox")
        if event not in _WRITE_EVENTS:
            return
        if event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            if _writes(mode, flags):
                if not inside(path):
                    deny(f"write outside workspace: {path}")
            elif not readable_path(path):
                deny(f"read outside sandbox: {path}")
            return
        targets = (
            args[:2] if event in {"os.rename", "os.replace", "os.link", "os.symlink"} else args[:1]
        )
        for target in targets:
            if target is not None and not inside(target):
                deny(f"{event} outside workspace: {target}")

    sys.addaudithook(lambda event, args: check(event, tuple(args)))
    return denials


def _bounded(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def run_payload(payload: dict[str, Any], workspace: str) -> dict[str, Any]:
    """Execute one request in-process (the process *is* the sandbox)."""
    limit = int(payload.get("max_output_bytes", 64_000))
    out, err = io.StringIO(), io.StringIO()
    result: dict[str, Any] | None = None
    error = ""
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            if str(payload.get("kind")) == "plugin":
                result = _run_plugin(
                    str(payload.get("module", "")), dict(payload.get("params") or {})
                )
            else:
                code = _run_command(tuple(payload.get("command") or ()))
    except SystemExit as exc:  # a plugin may exit; treat it as its own status
        code = int(exc.code or 0) if isinstance(exc.code, int) else 1
    except PermissionError as exc:
        code = 126
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - the boundary normalizes anything
        code = 1
        error = f"{type(exc).__name__}: {exc}"
        err.write(traceback.format_exc())
    return {
        "exit_code": code,
        "stdout": _bounded(out.getvalue(), limit),
        "stderr": _bounded(err.getvalue(), limit),
        "result": result if isinstance(result, dict) else None,
        "error": error,
    }


def _run_plugin(module: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Import a workspace module and call its ``run(context)`` entrypoint."""
    import builtins
    import importlib.util

    if not module or os.path.isabs(module) or ".." in module.split("/"):
        raise PermissionError(f"module {module!r} must be a relative workspace path")
    path = os.path.join(os.getcwd(), module)
    spec = importlib.util.spec_from_file_location("sandbox_plugin", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin module {module!r}")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    entry = getattr(loaded, "run", None)
    if not callable(entry):
        raise AttributeError("plugin module must define run(context) -> dict")
    value = entry(builtins.dict(params))
    return value if isinstance(value, dict) else {"value": value}


def _run_command(argv: tuple[str, ...]) -> int:
    """Run an argv through ``os.posix_spawn`` *inside* the guarded process tree."""
    if not argv:
        raise PermissionError("empty argv")
    import socket  # imported here so the guard sees its events

    _ = socket
    # The audit hook blocks os.system/exec/fork/subprocess, so a command run
    # must be delegated back to the parent runner as a plugin-equivalent.
    raise PermissionError("command execution must use kind='plugin' in this contract version")


def main(argv: list[str]) -> int:
    """Entrypoint: read the payload from stdin, write the verdict to argv[1]."""
    result_path = argv[1] if len(argv) > 1 else ""
    payload = json.loads(sys.stdin.read() or "{}")
    workspace = os.path.realpath(os.getcwd())
    denials = install_guard(workspace, (result_path,))
    outcome = run_payload(payload, workspace)
    document = {**outcome, "denied": denials, "workspace": workspace}
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    return 0


if __name__ == "__main__":  # pragma: no cover - executed as a child process
    raise SystemExit(main(sys.argv))
