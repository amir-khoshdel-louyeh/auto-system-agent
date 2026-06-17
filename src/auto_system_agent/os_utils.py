import platform
import shutil
from collections import deque
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# P2.1a: install capability knowledge-graph (data only; resolution follows
# in P2.1b). Native managers carry fidelity 1.0, universal fallbacks lower.
# ---------------------------------------------------------------------------

#: Cost weights for provider ranking: availability * 0.5 + fidelity * 0.3
#: + privilege * 0.2 (all terms are penalties, lower total cost wins).
COST_WEIGHTS = {"availability": 0.5, "fidelity": 0.3, "privilege": 0.2}


@dataclass(frozen=True)
class Provider:
    """One package provider: precondition (os/distros) + binary + template."""

    name: str
    os_names: frozenset[str] = frozenset({"linux"})
    distros: frozenset[str] | None = None  # None = any distro on those OSes
    binaries: tuple[str, ...] = ()  # probed via shutil.which, first hit wins
    template: str = "sudo apt install -y {package}"
    needs_sudo: bool = True
    fidelity: float = 1.0  # 1.0 native manager, lower for universal fallbacks


def _native(os_name: str, distros: set[str], binary: str, template: str) -> Provider:
    return Provider(
        name=binary,
        os_names=frozenset({os_name}),
        distros=frozenset(distros),
        binaries=(binary,),
        template=template,
        needs_sudo=True,
        fidelity=1.0,
    )


CAPABILITIES: dict[str, list[Provider]] = {
    "install_package": [
        _native(
            "linux",
            {"ubuntu", "debian", "linuxmint", "pop", "elementary"},
            "apt",
            "sudo apt install -y {package}",
        ),
        _native(
            "linux",
            {"fedora", "rhel", "centos", "rocky", "almalinux"},
            "dnf",
            "sudo dnf install -y {package}",
        ),
        _native(
            "linux",
            {"arch", "manjaro", "endeavouros"},
            "pacman",
            "sudo pacman -S --noconfirm {package}",
        ),
        _native(
            "linux",
            {"opensuse-leap", "opensuse-tumbleweed", "sles"},
            "zypper",
            "sudo zypper install -y {package}",
        ),
        _native("linux", {"alpine"}, "apk", "sudo apk add {package}"),
        Provider(
            name="brew",
            os_names=frozenset({"macos"}),
            distros=None,
            binaries=("brew",),
            template="brew install --cask {package}",
            needs_sudo=False,
            fidelity=1.0,
        ),
        Provider(
            name="winget",
            os_names=frozenset({"windows"}),
            distros=None,
            binaries=("winget",),
            template="winget install {package}",
            needs_sudo=False,
            fidelity=1.0,
        ),
        Provider(
            name="choco",
            os_names=frozenset({"windows"}),
            distros=None,
            binaries=("choco",),
            template="choco install -y {package}",
            needs_sudo=True,
            fidelity=0.6,
        ),
        Provider(
            name="snap",
            os_names=frozenset({"linux"}),
            distros=None,
            binaries=("snap",),
            template="sudo snap install {package}",
            needs_sudo=True,
            fidelity=0.6,
        ),
        Provider(
            name="flatpak",
            os_names=frozenset({"linux"}),
            distros=None,
            binaries=("flatpak",),
            template="flatpak install -y flathub {package}",
            needs_sudo=False,
            fidelity=0.6,
        ),
    ]
}


def provider_cost(provider: Provider, available: frozenset[str] | set[str]) -> float:
    """Penalty cost: availability * 0.5 + infidelity * 0.3 + privilege * 0.2."""
    missing = not any(binary in available for binary in provider.binaries)
    return (
        COST_WEIGHTS["availability"] * (1.0 if missing else 0.0)
        + COST_WEIGHTS["fidelity"] * (1.0 - provider.fidelity)
        + COST_WEIGHTS["privilege"] * (1.0 if provider.needs_sudo else 0.0)
    )


def _provider_eligible(provider: Provider, os_name: str, distro_id: str) -> bool:
    if os_name not in provider.os_names:
        return False
    if provider.distros is not None and distro_id not in provider.distros:
        return False
    return True


def resolve(
    capability: str,
    *,
    os_name: str,
    distro_id: str = "unknown",
    available: frozenset[str] | set[str] | None = None,
) -> list[Provider]:
    """BFS over the capability graph, eligible providers ranked by cost.

    The graph fans out capability -> providers; edges are guarded by
    preconditions (os/distro). BFS order is hops-first, ties broken by
    ascending cost, so the head is the cheapest reachable provider.
    Unknown capabilities yield an empty chain.
    """
    if available is None:
        available = frozenset()
    frontier: deque[str] = deque([capability])
    seen: set[str] = {capability}
    found: list[Provider] = []
    while frontier:
        node = frontier.popleft()
        for provider in CAPABILITIES.get(node, []):
            if not _provider_eligible(provider, os_name, distro_id):
                continue
            found.append(provider)
            # Providers are leaves today; keep BFS structure for multi-hop
            # capabilities (e.g. install_package -> sandbox -> runtime).
            if provider.name not in seen and provider.name in CAPABILITIES:
                seen.add(provider.name)
                frontier.append(provider.name)
    found.sort(key=lambda provider: (provider_cost(provider, available), provider.name))
    return found


def live_available_binaries() -> frozenset[str]:
    """Probe PATH for every binary named in the capability graph."""
    names: set[str] = set()
    for providers in CAPABILITIES.values():
        for provider in providers:
            names.update(provider.binaries)
    return frozenset(name for name in names if shutil.which(name) is not None)


def detect_os() -> str:
    """Returns one of: windows, linux, macos."""
    system_name = platform.system().lower()
    if "windows" in system_name:
        return "windows"
    if "darwin" in system_name:
        return "macos"
    return "linux"


def detect_linux_distro() -> str:
    """Returns Linux distro id from /etc/os-release when available."""
    os_release_path = Path("/etc/os-release")
    if not os_release_path.exists():
        return "unknown"

    try:
        lines = os_release_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"

    for line in lines:
        if line.startswith("ID="):
            return line.split("=", maxsplit=1)[1].strip().strip('"').lower()
    return "unknown"


def detect_linux_package_manager() -> str:
    """Maps distro id to package manager family."""
    distro_id = detect_linux_distro()
    if distro_id in {"ubuntu", "debian", "linuxmint", "pop", "elementary"}:
        return "apt"
    if distro_id in {"fedora", "rhel", "centos", "rocky", "almalinux"}:
        return "dnf"
    if distro_id in {"arch", "manjaro", "endeavouros"}:
        return "pacman"
    return "unknown"
