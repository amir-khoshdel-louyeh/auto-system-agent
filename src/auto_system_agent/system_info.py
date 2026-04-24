"""System architecture / OS / hardware detection for command generation.

Detects OS, distro, version, arch, kernel, CPU, RAM, and available
package managers (apt/dnf/pacman/snap/flatpak/brew/zypper/apk) so the
LLM can generate correct install commands.
"""
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SystemConfig:
    os_name: str = "linux"  # linux/windows/macos
    distro_id: str = "unknown"  # ubuntu/fedora/arch/...
    distro_version: str = ""  # e.g. 22.04
    distro_pretty: str = ""  # e.g. Ubuntu 22.04.5 LTS
    arch: str = "x86_64"  # x86_64/aarch64/...
    kernel: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_gb: float = 0.0
    package_manager: str = "apt"  # primary: apt/dnf/pacman/zypper/apk/brew/winget/unknown
    snap_available: bool = False
    flatpak_available: bool = False
    brew_available: bool = False
    hardware_summary: str = ""

    def to_prompt_fragment(self) -> str:
        """Compact fragment injected into LLM planner prompt."""
        parts = [
            f"OS: {self.os_name}",
            f"Distro: {self.distro_id} {self.distro_version} ({self.distro_pretty})".strip(),
            f"Arch: {self.arch}",
            f"Kernel: {self.kernel}",
            f"CPU: {self.cpu_model} ({self.cpu_cores} cores)",
            f"RAM: {self.ram_gb:.1f} GB",
            f"Primary package manager: {self.package_manager}",
            f"Snap: {'yes' if self.snap_available else 'no'}",
            f"Flatpak: {'yes' if self.flatpak_available else 'no'}",
            f"Brew: {'yes' if self.brew_available else 'no'}",
        ]
        if self.hardware_summary:
            parts.append(f"Hardware: {self.hardware_summary}")
        return " | ".join(p for p in parts if p)


def _read_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.exists():
        return data
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return data


def _detect_cpu() -> tuple[str, int]:
    model = platform.processor() or ""
    cores = 0
    try:
        cores = len([l for l in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines() if l.startswith("model name")])
    except Exception:
        try:
            import os as _os
            cores = _os.cpu_count() or 0
        except Exception:
            cores = 0
    if not model:
        try:
            # lscpu fallback
            out = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=2)
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    if "Model name" in line:
                        model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
    if not model:
        model = platform.machine()
    return model, cores


def _detect_ram_gb() -> float:
    try:
        # /proc/meminfo MemTotal in kB
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal"):
                kb = int(re.findall(r"\d+", line)[0])
                return round(kb / 1024 / 1024, 1)
    except Exception:
        pass
    return 0.0


def _detect_arch() -> str:
    m = platform.machine() or "x86_64"
    # normalize
    if m in ("AMD64", "x86_64", "x64"):
        return "x86_64"
    if m in ("aarch64", "arm64", "ARM64"):
        return "aarch64"
    return m


def _detect_package_manager(distro_id: str) -> str:
    # Check which managers are actually available, prefer distro default
    has_apt = shutil.which("apt") is not None
    has_dnf = shutil.which("dnf") is not None
    has_pacman = shutil.which("pacman") is not None
    has_zypper = shutil.which("zypper") is not None
    has_apk = shutil.which("apk") is not None
    has_brew = shutil.which("brew") is not None

    distro_map = {
        "ubuntu": "apt", "debian": "apt", "linuxmint": "apt", "pop": "apt",
        "fedora": "dnf", "rhel": "dnf", "centos": "dnf", "rocky": "dnf", "almalinux": "dnf",
        "arch": "pacman", "manjaro": "pacman", "endeavouros": "pacman",
        "opensuse-leap": "zypper", "opensuse-tumbleweed": "zypper",
        "alpine": "apk",
    }
    preferred = distro_map.get(distro_id, "unknown")
    # If preferred is available, use it; else fallback to any available
    if preferred != "unknown" and shutil.which(preferred) is not None:
        return preferred
    for cand in ("apt", "dnf", "pacman", "zypper", "apk"):
        if shutil.which(cand) is not None:
            return cand
    if has_brew:
        return "brew"
    return preferred if preferred != "unknown" else "unknown"


def detect_system_config() -> SystemConfig:
    os_name_raw = platform.system().lower()
    if "windows" in os_name_raw:
        os_name = "windows"
    elif "darwin" in os_name_raw:
        os_name = "macos"
    else:
        os_name = "linux"

    os_release = _read_os_release()
    distro_id = os_release.get("ID", "unknown").lower() or "unknown"
    # fedora's ID is fedora, ubuntu ubuntu, etc.
    if os_name != "linux":
        distro_id = os_name
    distro_version = os_release.get("VERSION_ID", "")
    distro_pretty = os_release.get("PRETTY_NAME", "")
    # fallback pretty from NAME + VERSION
    if not distro_pretty and os_release:
        distro_pretty = f"{os_release.get('NAME','')} {distro_version}".strip()

    arch = _detect_arch()
    kernel = platform.release() or ""
    cpu_model, cpu_cores = _detect_cpu()
    ram_gb = _detect_ram_gb()
    pkg = _detect_package_manager(distro_id) if os_name == "linux" else ("brew" if shutil.which("brew") else "unknown")
    if os_name == "windows":
        pkg = "winget"
    snap_available = shutil.which("snap") is not None
    flatpak_available = shutil.which("flatpak") is not None
    brew_available = shutil.which("brew") is not None

    hw_summary = ""
    try:
        # brief hardware: CPU cores + RAM
        hw_summary = f"{cpu_cores} cores, {ram_gb} GB RAM"
        if cpu_model:
            hw_summary = f"{cpu_model} | " + hw_summary
    except Exception:
        pass

    return SystemConfig(
        os_name=os_name,
        distro_id=distro_id,
        distro_version=distro_version,
        distro_pretty=distro_pretty or distro_id,
        arch=arch,
        kernel=kernel,
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        package_manager=pkg,
        snap_available=snap_available,
        flatpak_available=flatpak_available,
        brew_available=brew_available,
        hardware_summary=hw_summary,
    )


def system_config_to_dict(cfg: SystemConfig) -> dict:
    return asdict(cfg)


def system_config_from_dict(data: dict | None) -> SystemConfig:
    if not isinstance(data, dict):
        return detect_system_config()
    try:
        # only take known fields
        fields = {k: v for k, v in data.items() if k in SystemConfig.__dataclass_fields__}
        # coerce types
        if "cpu_cores" in fields:
            try:
                fields["cpu_cores"] = int(fields["cpu_cores"])
            except Exception:
                fields["cpu_cores"] = 0
        if "ram_gb" in fields:
            try:
                fields["ram_gb"] = float(fields["ram_gb"])
            except Exception:
                fields["ram_gb"] = 0.0
        for k in ("snap_available", "flatpak_available", "brew_available"):
            if k in fields:
                fields[k] = bool(fields[k])
        return SystemConfig(**fields)
    except Exception:
        return detect_system_config()
