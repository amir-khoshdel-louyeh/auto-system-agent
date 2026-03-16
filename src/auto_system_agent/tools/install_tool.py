import json
from pathlib import Path
from typing import List

from auto_system_agent.models import ExecutionResult
from auto_system_agent.os_utils import detect_os


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
        command = ["sudo", "apt", "install", "-y", package_name]
    elif os_name == "macos":
        command = ["brew", "install", package_name]
    else:
        command = ["winget", "install", "--id", package_name, "-e"]

    return ExecutionResult(
        success=True,
        message=f"Install command prepared for {app_name}.",
        data={"command": command},
    )
