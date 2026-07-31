"""Phase 5 item [47]: canonical payload hashing and hash-chain verification.

Payloads hash canonically (sorted keys), so equal content always hashes
equally. Any tampered or missing row breaks verification, and the report
names the *first* broken sequence rather than returning a generic failure.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from storage.audit import (
    GENESIS_HASH,
    AuditStore,
    canonical_payload,
    chain_hash,
)


class CanonicalHashingTests(unittest.TestCase):
    def test_key_order_does_not_change_the_payload(self):
        self.assertEqual(canonical_payload({"b": 1, "a": 2}), canonical_payload({"a": 2, "b": 1}))

    def test_chain_hash_is_deterministic(self):
        args = (GENESIS_HASH, 0, "run.started", {"a": 1})
        self.assertEqual(chain_hash(*args), chain_hash(*args))

    def test_every_field_changes_the_hash(self):
        base = chain_hash(GENESIS_HASH, 0, "run.started", {"a": 1})
        self.assertNotEqual(base, chain_hash("1" * 64, 0, "run.started", {"a": 1}))
        self.assertNotEqual(base, chain_hash(GENESIS_HASH, 1, "run.started", {"a": 1}))
        self.assertNotEqual(base, chain_hash(GENESIS_HASH, 0, "run.finished", {"a": 1}))
        self.assertNotEqual(base, chain_hash(GENESIS_HASH, 0, "run.started", {"a": 2}))


class ChainVerificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.store = AuditStore(self.db)
        for n in range(4):
            self.store.append("run-1", "node.finished", {"n": n})

    def test_fresh_chain_verifies(self):
        result = self.store.verify_chain("run-1")
        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 4)
        self.assertIsNone(result.first_broken_seq)

    def test_empty_chain_verifies(self):
        self.assertTrue(self.store.verify_chain("ghost").ok)

    def _tamper(self, seq, **fields):
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                f"UPDATE audit_events SET {assignments} WHERE execution_id = ? AND seq = ?",
                (*fields.values(), "run-1", seq),
            )

    def test_tampered_payload_is_detected_at_its_sequence(self):
        self._tamper(2, payload='{"n": 999}')
        result = self.store.verify_chain("run-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.first_broken_seq, 2)
        self.assertIn("content", result.reason)

    def test_relinked_row_is_detected_at_the_next_link(self):
        # Rewrite both the payload and its hash, but not the successor's
        # prev_hash; the chain still breaks, at seq 3.
        bad_payload = canonical_payload({"n": 999})
        bad_hash = chain_hash(
            self.store.events("run-1")[1]["hash"], 2, "node.finished", bad_payload
        )
        self._tamper(2, payload=bad_payload, hash=bad_hash)
        result = self.store.verify_chain("run-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.first_broken_seq, 3)
        self.assertIn("previous hash", result.reason)

    def test_missing_middle_row_is_detected(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "DELETE FROM audit_events WHERE execution_id = ? AND seq = ?", ("run-1", 1)
            )
        result = self.store.verify_chain("run-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.first_broken_seq, 2)


if __name__ == "__main__":
    unittest.main()
