import platform
from pathlib import Path


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
