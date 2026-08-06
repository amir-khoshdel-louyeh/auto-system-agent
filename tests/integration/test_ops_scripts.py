"""Post-RC items [100]-[103]: the operational scripts, driven end to end.

Each script is exercised the way an operator runs it — against a real SQLite
file, with its real CLI entry point where it has one — so the tests prove the
job works, not just the helper function.
"""

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import init_db
from memory.consent import ConsentStore
from memory.states import PUBLIC
from memory.store import MemoryStore
from scripts.anchor_audit import main as anchor_main
from scripts.anchor_audit import verify_anchor, write_anchor
from scripts.backup import (
    MANIFEST_SUFFIX,
    BackupError,
    backup_names,
    create_backup,
    offsite_copy,
    prune_backups,
    sha256_file,
)
from scripts.retention import run_retention
from storage.audit import AuditStore

PLAN = {
    "intent": "create_workspace",
    "steps": [{"id": "make", "type": "shell", "run": "mkdir -p demo"}],
}
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


class RetentionScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        consents = ConsentStore(self.db)
        consents.grant("alice")
        store = MemoryStore(self.db, consents=consents)
        self.old = store.save(PLAN, subject="alice", summary="old", intent="create_workspace")
        self.kept = store.save(
            {**PLAN, "steps": [{"id": "other", "type": "shell", "run": "true"}]},
            subject="alice",
            summary="recent",
            intent="create_workspace",
        )
        stamps = {
            self.old.id: (NOW - timedelta(days=400)).isoformat(),
            self.kept.id: (NOW - timedelta(days=2)).isoformat(),
        }
        with sqlite3.connect(str(self.db)) as conn:
            for item_id, stamp in stamps.items():
                conn.execute(
                    "UPDATE memory_items SET updated_at = ?, state = ? WHERE id = ?",
                    (stamp, PUBLIC, item_id),
                )
            # A quarantined row is the evidence behind "a failed plan cannot be
            # reused"; retention must never delete it.
            conn.execute(
                "UPDATE memory_items SET state = 'QUARANTINED', updated_at = ? WHERE id = ?",
                ((NOW - timedelta(days=400)).isoformat(), self.kept.id),
            )
        # Add a purgeable row that is old but still SANDBOX.
        self.sandbox = store.save(
            {**PLAN, "steps": [{"id": "third", "type": "shell", "run": "true"}]},
            subject="alice",
            summary="sandbox",
            intent="create_workspace",
        )
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute(
                "UPDATE memory_items SET state = 'SANDBOX', updated_at = ? WHERE id = ?",
                ((NOW - timedelta(days=400)).isoformat(), self.sandbox.id),
            )

    def test_dry_run_reports_without_deleting(self):
        report = run_retention(self.db, days=365, now=NOW.isoformat(), dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["purge_count"], 2)
        self.assertEqual(report["purged_count"], 0)
        self.assertFalse(report["audited"])
        self.assertEqual(MemoryStore(self.db).count(), 3)
        self.assertEqual(AuditStore(self.db).count(), 0)

    def test_purge_removes_only_the_selected_items_and_audits_it(self):
        report = run_retention(self.db, days=365, now=NOW.isoformat())
        self.assertEqual(report["purged_count"], 2)
        self.assertTrue(report["audited"])
        remaining = {item.id for item in MemoryStore(self.db).list_items(limit=100)}
        self.assertEqual(remaining, {self.kept.id}, "quarantined evidence survives")

        chains = AuditStore(self.db).execution_ids()
        self.assertEqual(len(chains), 1)
        self.assertTrue(chains[0].startswith("retention:"))
        self.assertTrue(AuditStore(self.db).verify(chains[0]))
        payload = json.loads(AuditStore(self.db).events(chains[0])[0]["payload"])
        self.assertEqual(payload["purged"], 2)
        self.assertEqual(payload["policy"]["max_age_days"], 365)
        self.assertNotIn(self.kept.id, payload["item_ids"])

    def test_purge_keeps_the_consent_history(self):
        run_retention(self.db, days=365, now=NOW.isoformat())
        with sqlite3.connect(str(self.db)) as conn:
            grants = conn.execute("SELECT COUNT(*) FROM consents").fetchone()[0]
        self.assertGreaterEqual(grants, 1)


class BackupPruneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        for day in (16, 17, 18):
            base = self.out / f"app-202610{day}T000000Z.db"
            base.write_bytes(b"snapshot")
            base.with_name(base.name + ".manifest.json").write_text("{}", encoding="utf-8")
        (self.out / "notes.txt").write_text("not a snapshot", encoding="utf-8")

    def test_dry_run_deletes_nothing(self):
        result = prune_backups(self.out, keep=2, dry_run=True)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["remove"], ["app-20261016T000000Z.db"])
        self.assertEqual(
            backup_names(self.out),
            [
                "app-20261016T000000Z.db",
                "app-20261017T000000Z.db",
                "app-20261018T000000Z.db",
            ],
        )

    def test_prune_removes_the_snapshot_and_its_manifest_only(self):
        result = prune_backups(self.out, keep=2)
        self.assertEqual(result["removed"], ["app-20261016T000000Z.db"])
        self.assertEqual(
            backup_names(self.out),
            ["app-20261017T000000Z.db", "app-20261018T000000Z.db"],
        )
        self.assertFalse((self.out / "app-20261016T000000Z.db.manifest.json").exists())
        self.assertTrue((self.out / "notes.txt").exists(), "unrelated files are untouched")

    def test_keep_zero_is_refused(self):
        with self.assertRaises(BackupError):
            prune_backups(self.out, keep=0)

    def test_offsite_copy_carries_the_manifest_and_verifies_the_hash(self):
        source = init_db(self.out / "app.db")
        AuditStore(source).append("run-1", "run.started", {})
        offsite = self.out / "offsite"
        target, manifest = create_backup(source, self.out / "snapshots", copy_to=offsite)
        copied = offsite / target.name
        self.assertTrue(copied.is_file())
        self.assertTrue((offsite / (target.name + MANIFEST_SUFFIX)).is_file())
        self.assertEqual(
            sha256_file(copied), json.loads(manifest.read_text())["database"]["sha256"]
        )

    def test_offsite_copy_refuses_to_invent_a_snapshot(self):
        with self.assertRaises(BackupError):
            offsite_copy(self.out / "missing.db", self.out / "offsite")


class AnchorScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.out = Path(self._tmp.name) / "anchors"
        store = AuditStore(self.db)
        for n in range(3):
            store.append("run-1", "node.result", {"n": n})
        store.append("run-2", "run.started", {})

    def test_anchor_then_verify_round_trip(self):
        target, document = write_anchor(self.db, self.out, key="k")
        self.assertTrue(target.is_file())
        self.assertEqual(document["chains"], 2)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded["digest"], document["digest"])
        report = verify_anchor(self.db, target, key="k")
        self.assertTrue(report["ok"], report["reason"])
        self.assertTrue(report["signed"])

    def test_a_rewritten_chain_fails_verification(self):
        target, _ = write_anchor(self.db, self.out)
        from storage.audit import GENESIS_HASH, canonical_payload, chain_hash

        rows = AuditStore(self.db).events("run-1")
        previous = GENESIS_HASH
        with sqlite3.connect(str(self.db)) as conn:
            for row in rows:
                payload = canonical_payload({"n": "changed"})
                hashed = chain_hash(previous, row["seq"], row["event"], payload)
                conn.execute(
                    "UPDATE audit_events SET payload = ?, prev_hash = ?, hash = ? WHERE id = ?",
                    (payload, previous, hashed, row["id"]),
                )
                previous = hashed
        self.assertTrue(AuditStore(self.db).verify("run-1"))
        report = verify_anchor(self.db, target)
        self.assertFalse(report["ok"])
        self.assertIn("rewritten chains", report["reason"])

    def test_the_cli_exit_status_is_the_verdict(self):
        target, _ = write_anchor(self.db, self.out, name="anchor.json")
        self.assertEqual(anchor_main(["verify", "--db", str(self.db), "--anchor", str(target)]), 0)
        target.write_text("{}", encoding="utf-8")
        self.assertEqual(anchor_main(["verify", "--db", str(self.db), "--anchor", str(target)]), 1)


if __name__ == "__main__":
    unittest.main()
