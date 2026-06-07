"""Pre-flight checks for shell commands before a terminal round-trip.

Catches obvious breakage (missing binary, macOS-only `open` on Linux,
`xdg-open` of a nonexistent file) instantly with an actionable message,
so the ReAct loop repairs from information instead of blind retries.
"""

import shlex
import shutil
from pathlib import Path

from auto_system_agent.os_utils import detect_os

# Handled inside TerminalSession / the pty shell, not real executables.
_SHELL_BUILTINS = {
    "cd", "pwd", "history", "clear", "exit", "echo", "true", "false", ":",
    "test", "[", "export", "source", ".", "alias", "unalias", "type",
    "command", "hash", "printf", "pushd", "popd", "dirs", "read", "time",
}

# Linux file/URL openers: a bare word that is not a file is an app guess.
_OPENERS = {"xdg-open", "gio", "gnome-open", "kde-open", "exo-open", "wslview"}


def check_command(command: str, cwd: Path | None = None) -> str | None:
    """Return an error message if the command is doomed, else None."""
    argv = _first_words(command)
    if not argv:
        return None
    prog = argv[0]
    if prog in _SHELL_BUILTINS:
        return None
    if shutil.which(prog) is None:
        return _missing_binary_hint(prog)
    if prog in _OPENERS and len(argv) > 1:
        return _opener_target_hint(prog, argv[1], cwd)
    return None


def _first_words(command: str) -> list[str]:
    try:
        tokens = shlex.split(command.strip(), posix=True)
    except ValueError:
        return []
    # Skip leading VAR=value assignments (e.g. `FOO=1 cmd`).
    out: list[str] = []
    for token in tokens:
        if not out and "=" in token and "/" not in token and not token.startswith("-"):
            continue
        out.append(token)
    return out


def _missing_binary_hint(prog: str) -> str:
    if prog == "open" and detect_os() == "linux":
        return (
            "command not found: open (no such file or directory in PATH). "
            "'open' is macOS-only; on Linux use 'xdg-open <file-or-URL>' for files, "
            "or launch apps via 'gtk-launch <name>.desktop' / 'flatpak run <app-id>'."
        )
    if prog == "start" and detect_os() == "linux":
        return (
            "command not found: start (no such file or directory in PATH). "
            "'start' is Windows-only; on Linux use 'xdg-open <file-or-URL>'."
        )
    return (
        f"command not found: {prog} (no such file or directory in PATH). "
        "It may not be installed on this system."
    )


def _opener_target_hint(opener: str, target: str, cwd: Path | None) -> str | None:
    lowered = target.lower()
    if "://" in target or lowered.startswith(("mailto:", "file:")):
        return None
    candidate = Path(target).expanduser()
    if candidate.is_absolute() and candidate.exists():
        return None
    if target.startswith(("~", "/", "./", "../")):
        # Explicit path that does not exist: let the shell report it.
        return None
    if cwd is not None:
        try:
            if (cwd / target).exists():
                return None
        except OSError:
            pass
    if "/" in target or target.startswith("-"):
        return None

    query = target[:-8] if target.lower().endswith(".desktop") else target
    matches = find_desktop_matches(query)
    if matches:
        lines = "\n".join(f"- {name} ({app_id})" for name, app_id in matches)
        return (
            f"No such file '{target}' for {opener}. Installed apps matching '{query}':\n"
            f"{lines}\n"
            f"Launch with: gtk-launch <name>.desktop — or run its Exec command ending with '&'."
        )
    return (
        f"No such file '{target}' for {opener}. No installed app matches '{query}'. "
        f"Check with: flatpak list --app | grep -i {query}; ls /usr/share/applications | grep -i {query}"
    )


def find_desktop_matches(query: str, dirs: list[Path] | None = None) -> list[tuple[str, str]]:
    """Search .desktop entries by app name; returns [(Name, file-id)]."""
    needle = query.strip().lower()
    if not needle:
        return []
    if dirs is None:
        home = Path.home()
        dirs = [
            home / ".local" / "share" / "applications",
            Path("/usr/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            home / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
        ]
    found: list[tuple[str, str]] = []
    for directory in dirs:
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for entry in entries:
            try:
                text = entry.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            name = _desktop_field(text, "Name")
            if needle in entry.stem.lower() or (name and needle in name.lower()):
                found.append((name or entry.stem, entry.name))
                if len(found) >= 5:
                    return found
    return found


def _desktop_field(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(key + "="):
            return line.split("=", maxsplit=1)[1].strip()
    return ""
