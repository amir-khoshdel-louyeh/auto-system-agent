import shutil
from pathlib import Path

from auto_system_agent.models import ExecutionResult


PROTECTED_PATHS = {
    Path("/").resolve(),
    Path.home().resolve(),
}


def _is_protected(path: Path) -> bool:
    return path in PROTECTED_PATHS


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


def move_path(source_text: str, destination_text: str) -> ExecutionResult:
    source = Path(source_text).expanduser().resolve()
    destination = Path(destination_text).expanduser().resolve()

    if not source.exists():
        return ExecutionResult(success=False, message=f"Path does not exist: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    moved_to = shutil.move(str(source), str(destination))
    return ExecutionResult(success=True, message=f"Moved to: {moved_to}")


def delete_path(path_text: str) -> ExecutionResult:
    target = Path(path_text).expanduser().resolve()
    if not target.exists():
        return ExecutionResult(success=False, message=f"Path does not exist: {target}")

    if _is_protected(target):
        return ExecutionResult(success=False, message=f"Deletion blocked for protected path: {target}")

    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()

    return ExecutionResult(success=True, message=f"Deleted: {target}")
