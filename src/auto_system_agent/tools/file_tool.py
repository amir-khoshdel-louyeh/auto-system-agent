import shutil
from pathlib import Path
import zipfile

from auto_system_agent.models import ExecutionResult


SYSTEM_PROTECTED_ROOTS = {
    Path("/bin").resolve(),
    Path("/boot").resolve(),
    Path("/dev").resolve(),
    Path("/etc").resolve(),
    Path("/lib").resolve(),
    Path("/lib64").resolve(),
    Path("/proc").resolve(),
    Path("/run").resolve(),
    Path("/sbin").resolve(),
    Path("/sys").resolve(),
    Path("/usr").resolve(),
    Path("/var").resolve(),
}

USER_PROTECTED_PATHS = {
    Path.home().resolve(),
}


def _is_protected(path: Path) -> bool:
    if path == Path("/").resolve():
        return True

    for root in SYSTEM_PROTECTED_ROOTS:
        if path == root or root in path.parents:
            return True

    return path in USER_PROTECTED_PATHS


def create_folder(path_text: str) -> ExecutionResult:
    try:
        path = Path(path_text).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not create folder: {exc}")

    return ExecutionResult(success=True, message=f"Folder ready: {path}")


def compress_path(path_text: str) -> ExecutionResult:
    try:
        source = Path(path_text).expanduser().resolve()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not resolve path: {exc}")

    if not source.exists():
        return ExecutionResult(success=False, message=f"Path does not exist: {source}")

    try:
        if source.is_dir():
            archive_path = shutil.make_archive(
                str(source),
                "zip",
                root_dir=str(source.parent),
                base_dir=source.name,
            )
        else:
            archive_path = str(source.parent / f"{source.stem}.zip")
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_obj:
                zip_obj.write(source, arcname=source.name)
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not create archive: {exc}")

    return ExecutionResult(success=True, message=f"Created archive: {archive_path}")


def list_files(path_text: str) -> ExecutionResult:
    try:
        target = Path(path_text).expanduser().resolve()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not resolve path: {exc}")

    if not target.exists() or not target.is_dir():
        return ExecutionResult(success=False, message=f"Invalid directory: {target}")

    try:
        entries = sorted(item.name for item in target.iterdir())
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not list directory: {exc}")

    listing = "\n".join(entries) if entries else "(empty)"
    return ExecutionResult(success=True, message=f"Contents of {target}:\n{listing}")


def move_path(source_text: str, destination_text: str) -> ExecutionResult:
    try:
        source = Path(source_text).expanduser().resolve()
        destination = Path(destination_text).expanduser().resolve()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not resolve path: {exc}")

    if not source.exists():
        return ExecutionResult(success=False, message=f"Path does not exist: {source}")

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        moved_to = shutil.move(str(source), str(destination))
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not move path: {exc}")

    return ExecutionResult(success=True, message=f"Moved to: {moved_to}")


def delete_path(path_text: str) -> ExecutionResult:
    try:
        target = Path(path_text).expanduser().resolve()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not resolve path: {exc}")

    if not target.exists():
        return ExecutionResult(success=False, message=f"Path does not exist: {target}")

    if _is_protected(target):
        return ExecutionResult(success=False, message=f"Deletion blocked for protected path: {target}")

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not delete path: {exc}")

    return ExecutionResult(success=True, message=f"Deleted: {target}")
