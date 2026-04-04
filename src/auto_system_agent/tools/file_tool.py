import shutil
import os
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


def _get_sandbox_roots() -> list[Path]:
    raw_roots = os.getenv("AUTO_AGENT_PATH_ALLOW_ROOTS", "").strip()
    if not raw_roots:
        return []
    roots: list[Path] = []
    for item in raw_roots.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        try:
            roots.append(Path(item).expanduser().resolve())
        except OSError:
            continue
    return roots


def _is_in_sandbox(path: Path) -> bool:
    roots = _get_sandbox_roots()
    if not roots:
        return True
    return any(path == root or root in path.parents for root in roots)


def _sandbox_block(path: Path) -> ExecutionResult:
    return ExecutionResult(success=False, message=f"Path is outside sandbox allow-list roots: {path}")


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
        if not _is_in_sandbox(path):
            return _sandbox_block(path)
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
    if not _is_in_sandbox(source):
        return _sandbox_block(source)

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
    if not _is_in_sandbox(target):
        return _sandbox_block(target)

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
    if not _is_in_sandbox(source):
        return _sandbox_block(source)
    if not _is_in_sandbox(destination):
        return _sandbox_block(destination)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        moved_to = shutil.move(str(source), str(destination))
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not move path: {exc}")

    return ExecutionResult(success=True, message=f"Moved to: {moved_to}")


def copy_path(source_text: str, destination_text: str) -> ExecutionResult:
    try:
        source = Path(source_text).expanduser().resolve()
        destination = Path(destination_text).expanduser().resolve()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not resolve path: {exc}")

    if not source.exists() or not source.is_file():
        return ExecutionResult(success=False, message=f"Invalid file source: {source}")
    if not _is_in_sandbox(source):
        return _sandbox_block(source)
    if not _is_in_sandbox(destination):
        return _sandbox_block(destination)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        copied_to = shutil.copy2(str(source), str(destination))
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not copy path: {exc}")

    return ExecutionResult(success=True, message=f"Copied to: {copied_to}")


def create_empty_file(path_text: str) -> ExecutionResult:
    try:
        target = Path(path_text).expanduser().resolve()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not resolve path: {exc}")

    if not _is_in_sandbox(target):
        return _sandbox_block(target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not create file: {exc}")

    return ExecutionResult(success=True, message=f"File ready: {target}")


def delete_path(path_text: str) -> ExecutionResult:
    try:
        target = Path(path_text).expanduser().resolve()
    except OSError as exc:
        return ExecutionResult(success=False, message=f"Could not resolve path: {exc}")

    if not target.exists():
        return ExecutionResult(success=False, message=f"Path does not exist: {target}")
    if not _is_in_sandbox(target):
        return _sandbox_block(target)

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
