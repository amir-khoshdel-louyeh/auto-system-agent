import shutil
from pathlib import Path

from auto_system_agent.models import ExecutionResult


def create_folder(path_text: str) -> ExecutionResult:
    path = Path(path_text).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return ExecutionResult(success=True, message=f"Folder ready: {path}")


def compress_path(path_text: str) -> ExecutionResult:
    source = Path(path_text).expanduser().resolve()
    if not source.exists():
        return ExecutionResult(success=False, message=f"Path does not exist: {source}")

    archive_path = shutil.make_archive(str(source), "zip", str(source))
    return ExecutionResult(success=True, message=f"Created archive: {archive_path}")


def list_files(path_text: str) -> ExecutionResult:
    target = Path(path_text).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        return ExecutionResult(success=False, message=f"Invalid directory: {target}")

    entries = sorted(item.name for item in target.iterdir())
    listing = "\n".join(entries) if entries else "(empty)"
    return ExecutionResult(success=True, message=f"Contents of {target}:\n{listing}")
