import json
import os
import shutil
from pathlib import Path
from typing import List

from auto_system_agent.models import ExecutionResult
from auto_system_agent.os_utils import detect_linux_package_manager, detect_os


def _load_app_library() -> dict:
    data_path = Path(__file__).resolve().parent.parent / "data" / "app_library.json"
    with data_path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def get_known_app_names() -> list[str]:
    library = _load_app_library()
    return sorted(library.keys(), key=len, reverse=True)


def extract_known_apps(text: str) -> list[str]:
    lowered = text.lower()
    return [app_name for app_name in get_known_app_names() if app_name in lowered]


def _build_linux_install_command(package_name: str) -> list[str] | None:
    package_manager = detect_linux_package_manager()
    if package_manager == "apt":
        return ["sudo", "apt", "install", "-y", package_name]
    if package_manager == "dnf":
        return ["sudo", "dnf", "install", "-y", package_name]
    if package_manager == "pacman":
        return ["sudo", "pacman", "-S", "--noconfirm", package_name]
    return None


def verify_install_environment(command: list[str]) -> ExecutionResult:
    if not command:
        return ExecutionResult(success=False, message="Install command is empty.")

    executable = command[0]
    if executable == "sudo":
        if shutil.which("sudo") is None:
            return ExecutionResult(success=False, message="sudo is required but not available on this system.")

        if len(command) < 2:
            return ExecutionResult(success=False, message="Install command missing package manager executable.")

        package_manager = command[1]
        if shutil.which(package_manager) is None:
            return ExecutionResult(success=False, message=f"Package manager not found: {package_manager}")

        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return ExecutionResult(success=True, message="Install environment verified.")

        return ExecutionResult(success=True, message="Install environment verified. sudo may ask for password.")

    if shutil.which(executable) is None:
        return ExecutionResult(success=False, message=f"Install executable not found: {executable}")

    return ExecutionResult(success=True, message="Install environment verified.")


def build_install_command(app_name: str) -> ExecutionResult:
    """Builds a package-manager command for the requested app."""
    if not app_name:
        return ExecutionResult(success=False, message="No application name was provided.")

    library = _load_app_library()
    normalized = app_name.strip().lower()
    os_name = detect_os()

    if normalized not in library or os_name not in library[normalized]:
        return ExecutionResult(
            success=False,
            message=f"Unsupported application '{app_name}' for {os_name}.",
        )

    package_name = library[normalized][os_name]
    command: List[str]
    if os_name == "linux":
        linux_command = _build_linux_install_command(package_name)
        if linux_command is None:
            return ExecutionResult(
                success=False,
                message="Unsupported Linux distribution for installation automation.",
            )
        command = linux_command
    elif os_name == "macos":
        command = ["brew", "install", package_name]
    else:
        command = ["winget", "install", "--id", package_name, "-e"]

    return ExecutionResult(
        success=True,
        message=f"Install command prepared for {app_name}.",
        data={"command": command},
    )
