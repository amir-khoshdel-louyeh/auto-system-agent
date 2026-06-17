import platform
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
