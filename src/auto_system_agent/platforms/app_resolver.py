"""Dynamic app discovery: resolve display names without hard-coded lists.

Sources are always read from the live system, never from a baked-in
catalog: .desktop entries (what is written below the app icon), installed
flatpak refs, and installed system packages (rpm/dpkg/pacman). Results
are cached briefly so repeated checks stay cheap.
"""

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_CACHE_TTL = 60.0
_cache: dict[str, tuple[float, object]] = {}


@dataclass(frozen=True)
class AppCandidate:
    """One resolvable app: display name, removable identifier, source."""

    display: str
    identifier: str
    source: str  # desktop | flatpak | system
    exact: bool = False


def _cached(key: str, builder):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = builder()
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = (now, value)
    return value


def _run_capture(argv: list[str], timeout: int = 10) -> str | None:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or ""


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def desktop_candidates(text: str, dirs: list[Path] | None = None) -> list[AppCandidate]:
    """Apps whose .desktop Name or file id matches (the icon label)."""
    from auto_system_agent.safety.command_guard import find_desktop_matches

    needle = _normalize(text)
    if not needle:
        return []
    found: list[AppCandidate] = []
    try:
        matches = find_desktop_matches(needle, dirs=dirs) if dirs is not None else find_desktop_matches(needle)
    except Exception:
        return []
    for name, file_id in matches:
        lowered_name = (name or "").lower()
        found.append(
            AppCandidate(
                display=name or file_id,
                identifier=file_id,
                source="desktop",
                exact=needle == lowered_name or needle == file_id.lower().removesuffix(".desktop"),
            )
        )
    return found


def _flatpak_table() -> list[tuple[str, str]]:
    """Installed (ref, name) rows, cached; empty when flatpak is absent."""

    def _load():
        if shutil.which("flatpak") is None:
            return []
        output = _run_capture(["flatpak", "list", "--app", "--columns=application,name"])
        if not output:
            return []
        rows = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip():
                rows.append((parts[0].strip(), parts[1].strip() or parts[0].strip()))
        return rows

    try:
        return list(_cached("flatpak_table", _load))
    except Exception:
        return []


def flatpak_candidates(text: str, table: list[tuple[str, str]] | None = None) -> list[AppCandidate]:
    """Installed flatpak refs whose ref or name matches."""
    needle = _normalize(text)
    if not needle:
        return []
    rows = table if table is not None else _flatpak_table()
    found = []
    for ref, name in rows:
        if needle in ref.lower() or needle in name.lower():
            found.append(
                AppCandidate(display=name, identifier=ref, source="flatpak", exact=needle in (ref.lower(), name.lower()))
            )
    return found


def _system_package_names() -> list[str]:
    """Installed system package names via the native query tool, cached."""

    def _load():
        if shutil.which("rpm") is not None:
            output = _run_capture(["rpm", "-qa", "--queryformat", "%{NAME}\n"])
            if output:
                return sorted({line.strip() for line in output.splitlines() if line.strip()})
        if shutil.which("dpkg-query") is not None:
            output = _run_capture(["dpkg-query", "-W", "-f=${Package}\n"])
            if output:
                return sorted({line.strip() for line in output.splitlines() if line.strip()})
        if shutil.which("pacman") is not None:
            output = _run_capture(["pacman", "-Qq"])
            if output:
                return sorted({line.strip() for line in output.splitlines() if line.strip()})
        return []

    try:
        return list(_cached("system_packages", _load))
    except Exception:
        return []


def system_package_candidates(text: str, packages: list[str] | None = None) -> list[AppCandidate]:
    """Installed system packages whose name contains the text."""
    needle = _normalize(text).replace(" ", "-")
    if not needle:
        return []
    names = packages if packages is not None else _system_package_names()
    return [
        AppCandidate(display=name, identifier=name, source="system", exact=name.lower() == needle)
        for name in names
        if needle in name.lower()
    ]


def resolve_app(text: str) -> list[AppCandidate]:
    """Ranked candidates across desktop, flatpak and system sources.

    Exact matches first, then by source stability (flatpak/system before
    bare desktop labels, which may be stale). No hard-coded app names.
    """
    needle = _normalize(text)
    if not needle:
        return []
    seen: set[tuple[str, str]] = set()
    ranked: list[AppCandidate] = []
    try:
        desktop = desktop_candidates(text)
    except Exception:
        desktop = []
    try:
        flatpak = flatpak_candidates(text)
    except Exception:
        flatpak = []
    try:
        system = system_package_candidates(text)
    except Exception:
        system = []
    for candidate in flatpak + system + desktop:
        key = (candidate.source, candidate.identifier)
        if key in seen:
            continue
        seen.add(key)
        ranked.append(candidate)
    ranked.sort(key=lambda item: (not item.exact, {"flatpak": 0, "system": 1, "desktop": 2}.get(item.source, 3)))
    return ranked
