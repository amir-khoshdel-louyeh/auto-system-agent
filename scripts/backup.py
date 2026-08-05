#!/usr/bin/env python3
"""Phase 9 item [89]: back up, verify and restore the SQLite state.

The API keeps everything durable in one SQLite file (users, roles,
workspaces, workflow versions, runs, approvals, memory, audit chain), so a
backup is that file plus the evidence that it is trustworthy:

- a **consistent snapshot** taken through ``sqlite3``'s online backup API, so
  a running writer cannot produce a torn copy;
- a **manifest** carrying the file's SHA-256 and size, every applied
  migration (version/name/checksum), table row counts, and the result of
  verifying *every* audit hash chain in the copy.

`verify` re-checks a backup against its manifest, and `restore` refuses to
overwrite an existing database (or a backup whose manifest does not match)
unless ``--force`` says so. Both commands are deliberately boring: no
compression, no encryption, no cloud — a copy you can inspect with
``sqlite3``.

Usage::

    python scripts/backup.py backup  --db data/app.db --out backups/
    python scripts/backup.py verify  backups/app-20261018T131900Z.db
    python scripts/backup.py restore backups/app-20261018T131900Z.db --db restored.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

MANIFEST_SUFFIX = ".manifest.json"

#: Tables whose row counts are recorded (and compared after a restore).
COUNTED_TABLES = (
    "users",
    "user_roles",
    "workspaces",
    "workflow_versions",
    "executions",
    "execution_steps",
    "approval_requests",
    "approvals",
    "audit_events",
    "memory_items",
)


class BackupError(RuntimeError):
    """The backup or its manifest is not trustworthy."""


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(source: Path, target: Path) -> None:
    """Consistent copy of a live SQLite database."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as source_conn, sqlite3.connect(str(target)) as target_conn:
        source_conn.backup(target_conn)


def table_counts(path: Path) -> dict[str, int]:
    """Row counts for the counted tables (missing tables are skipped)."""
    counts: dict[str, int] = {}
    with sqlite3.connect(str(path)) as conn:
        present = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table in COUNTED_TABLES:
            if table in present:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
    return counts


def applied_migrations(path: Path) -> list[dict[str, str]]:
    """Applied migrations as recorded by the checksum-tracked runner."""
    with sqlite3.connect(str(path)) as conn:
        try:
            rows = conn.execute(
                "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {"version": row[0], "name": row[1], "checksum": row[2], "applied_at": row[3]}
        for row in rows
    ]


def audit_summary(path: Path) -> dict[str, Any]:
    """Verify every execution's hash chain inside a copy (FR-12)."""
    from storage.audit import AuditStore

    store = AuditStore(path)
    with sqlite3.connect(str(path)) as conn:
        try:
            executions = [
                row[0] for row in conn.execute("SELECT DISTINCT execution_id FROM audit_events")
            ]
        except sqlite3.OperationalError:
            executions = []
    broken: list[dict[str, Any]] = []
    checked = 0
    for execution_id in sorted(executions):
        result = store.verify_chain(execution_id)
        checked += result.checked
        if not result.ok:
            broken.append(
                {
                    "execution_id": execution_id,
                    "first_broken_seq": result.first_broken_seq,
                    "reason": result.reason,
                }
            )
    return {
        "executions": len(executions),
        "events_verified": checked,
        "chains_ok": not broken,
        "broken": broken,
    }


def build_manifest(source: Path) -> dict[str, Any]:
    """Manifest for one database file."""
    from app.settings import Settings

    return {
        "created_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "app_version": Settings().app_version,
        "database": {
            "sha256": sha256_file(source),
            "size_bytes": source.stat().st_size,
        },
        "migrations": applied_migrations(source),
        "tables": table_counts(source),
        "audit": audit_summary(source),
    }


def create_backup(db: str | Path, out_dir: str | Path, *, name: str = "") -> tuple[Path, Path]:
    """Snapshot ``db`` into ``out_dir`` and write its manifest."""
    source = Path(db)
    if not source.is_file():
        raise BackupError(f"no database at {source}")
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = name or f"{source.stem}-{stamp}"
    target = Path(out_dir) / f"{stem}.db"
    snapshot(source, target)
    manifest = build_manifest(target)
    manifest_path = target.with_name(target.name + MANIFEST_SUFFIX)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target, manifest_path


def read_manifest(backup: str | Path) -> dict[str, Any]:
    """Load the manifest next to a backup."""
    path = Path(f"{backup}{MANIFEST_SUFFIX}")
    if not path.is_file():
        raise BackupError(f"no manifest next to {backup} (expected {path.name})")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_backup(backup: str | Path) -> dict[str, Any]:
    """Re-check a backup against its manifest; raises when it does not match."""
    target = Path(backup)
    if not target.is_file():
        raise BackupError(f"no backup at {target}")
    manifest = read_manifest(target)
    recorded = manifest.get("database", {})
    actual_hash = sha256_file(target)
    if actual_hash != recorded.get("sha256"):
        raise BackupError(
            f"backup hash mismatch: manifest {recorded.get('sha256')} != actual {actual_hash}"
        )
    if int(recorded.get("size_bytes", -1)) != target.stat().st_size:
        raise BackupError("backup size does not match the manifest")
    counts = table_counts(target)
    for table, expected in (manifest.get("tables") or {}).items():
        if counts.get(table) != expected:
            raise BackupError(f"row count changed for {table}: {counts.get(table)} != {expected}")
    audit = audit_summary(target)
    if not audit["chains_ok"]:
        raise BackupError(f"audit chain broken in the backup: {audit['broken']}")
    return {"manifest": manifest, "tables": counts, "audit": audit}


def restore_backup(backup: str | Path, db: str | Path, *, force: bool = False) -> Path:
    """Copy a verified backup into ``db`` (refuses to clobber unless forced)."""
    verification = verify_backup(backup)
    target = Path(db)
    if target.exists() and not force:
        raise BackupError(f"{target} already exists; pass --force to overwrite it")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)
    if sha256_file(target) != verification["manifest"]["database"]["sha256"]:
        raise BackupError("restored copy does not match the verified backup")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="backup / verify / restore the SQLite state")
    sub = parser.add_subparsers(dest="command", required=True)

    backup_cmd = sub.add_parser("backup", help="snapshot a database and write a manifest")
    backup_cmd.add_argument("--db", required=True)
    backup_cmd.add_argument("--out", required=True, help="output directory")
    backup_cmd.add_argument("--name", default="", help="file stem for the snapshot")

    verify_cmd = sub.add_parser("verify", help="check a backup against its manifest")
    verify_cmd.add_argument("backup")

    restore_cmd = sub.add_parser("restore", help="copy a verified backup into a database path")
    restore_cmd.add_argument("backup")
    restore_cmd.add_argument("--db", required=True)
    restore_cmd.add_argument("--force", action="store_true", help="overwrite an existing database")

    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            target, manifest = create_backup(args.db, args.out, name=args.name)
            print(f"backup written: {target}")
            print(f"manifest:       {manifest}")
            return 0
        if args.command == "verify":
            result = verify_backup(args.backup)
            audit = result["audit"]
            print(
                f"backup verified: {result['manifest']['database']['sha256'][:12]}… "
                f"{len(result['tables'])} tables, "
                f"{audit['events_verified']} audit events in {audit['executions']} chains"
            )
            return 0
        target = restore_backup(args.backup, args.db, force=args.force)
        print(f"restored to: {target}")
        return 0
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
