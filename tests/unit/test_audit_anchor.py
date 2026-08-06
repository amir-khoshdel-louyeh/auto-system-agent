"""Post-RC item [103]: the anchor catches what a hash chain cannot.

`verify_chain` proves a chain is internally consistent. The anchor exists for
the case it cannot see: an attacker rewriting **every** row of a chain,
hashes included, so the chain still verifies but its head is different. That
scenario is constructed here on a real SQLite file, and the anchor is shown to
catch it while `verify_chain` passes.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from storage.anchor import (
    ANCHOR_KEY_ENV,
    ANCHOR_VERSION,
    build_document,
    canonical,
    chain_heads,
    digest,
    load_document,
    sign,
    verify_document,
)
from storage.audit import GENESIS_HASH, AuditStore, chain_hash

KEY = "anchor-test-key"


def _seed(tmp: str) -> tuple[Path, AuditStore]:
    db = init_db(Path(tmp) / "app.db")
    store = AuditStore(db)
    for n in range(3):
        store.append("run-a", "node.result", {"n": n})
    store.append("run-b", "run.started", {"workflow": "demo"})
    return db, store


def _rewrite_chain(db: Path, execution_id: str, payload_for) -> None:
    """Rewrite every row of one chain *consistently* (the attack)."""
    rows = AuditStore(db).events(execution_id)
    previous = GENESIS_HASH
    with sqlite3.connect(str(db)) as conn:
        for row in rows:
            payload = canonical(payload_for(row))
            hashed = chain_hash(previous, row["seq"], row["event"], payload)
            conn.execute(
                "UPDATE audit_events SET payload = ?, prev_hash = ?, hash = ? WHERE id = ?",
                (payload, previous, hashed, row["id"]),
            )
            previous = hashed


class AnchorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db, self.store = _seed(self._tmp.name)

    def test_heads_are_one_short_row_per_chain(self):
        heads = chain_heads(self.store)
        self.assertEqual([head["execution_id"] for head in heads], ["run-a", "run-b"])
        self.assertEqual(heads[0]["events"], 3)
        self.assertEqual(heads[0]["head_hash"], self.store.events("run-a")[-1]["hash"])
        self.assertTrue(all(set(head) == {"execution_id", "events", "head_hash"} for head in heads))

    def test_digest_is_stable_and_changes_with_any_head(self):
        heads = chain_heads(self.store)
        self.assertEqual(digest(heads), digest(chain_heads(self.store)))
        self.assertEqual(len(digest(heads)), 64)
        self.assertNotEqual(digest(heads), digest(heads[:-1]))

    def test_a_fresh_anchor_verifies(self):
        document = build_document(chain_heads(self.store), created_at="2026-10-18T00:00:00Z")
        check = verify_document(document, chain_heads(self.store))
        self.assertTrue(check.ok, check.reason)
        self.assertEqual(check.chains, 2)
        self.assertIsNone(check.signature_ok)
        self.assertEqual(document["anchor_version"], ANCHOR_VERSION)
        self.assertEqual(document["events"], 4)
        self.assertFalse(document["signed"])

    def test_an_added_chain_is_detected(self):
        document = build_document(chain_heads(self.store))
        self.store.append("run-c", "run.started", {})
        check = verify_document(document, chain_heads(self.store))
        self.assertFalse(check.ok)
        self.assertIn("new chains", check.reason)

    def test_a_removed_chain_is_detected(self):
        document = build_document(chain_heads(self.store))
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute("DELETE FROM audit_events WHERE execution_id = 'run-b'")
        check = verify_document(document, chain_heads(self.store))
        self.assertFalse(check.ok)
        self.assertIn("missing chains", check.reason)

    def test_a_consistent_full_chain_rewrite_passes_verify_and_fails_the_anchor(self):
        document = build_document(chain_heads(self.store), created_at="2026-10-18T00:00:00Z")
        _rewrite_chain(self.db, "run-a", lambda row: {"tampered": json.loads(row["payload"])})
        # The chain still verifies: every hash was recomputed consistently.
        self.assertTrue(AuditStore(self.db).verify_chain("run-a").ok)
        check = verify_document(document, chain_heads(AuditStore(self.db)))
        self.assertFalse(check.ok)
        self.assertIn("rewritten chains", check.reason)
        self.assertEqual(check.expected, document["digest"])

    def test_an_unsigned_document_cannot_be_verified_as_signed(self):
        document = build_document(chain_heads(self.store), key=KEY)
        self.assertTrue(document["signed"])
        self.assertEqual(document["signature"], sign(document, KEY))
        self.assertTrue(verify_document(document, chain_heads(self.store), key=KEY).ok)

        missing_key = verify_document(document, chain_heads(self.store))
        self.assertFalse(missing_key.ok)
        self.assertIn(ANCHOR_KEY_ENV, missing_key.reason)

    def test_a_tampered_anchor_document_is_detected_by_its_signature(self):
        document = build_document(chain_heads(self.store), key=KEY)
        document["created_at"] = "2030-01-01T00:00:00Z"
        check = verify_document(document, chain_heads(self.store), key=KEY)
        self.assertFalse(check.ok)
        self.assertFalse(check.signature_ok)
        self.assertIn("signature", check.reason)

    def test_a_wrong_version_is_refused(self):
        document = build_document(chain_heads(self.store))
        document["anchor_version"] = "anchor-v0"
        check = verify_document(document, chain_heads(self.store))
        self.assertFalse(check.ok)
        self.assertIn("version", check.reason)

    def test_documents_round_trip_through_json(self):
        document = build_document(chain_heads(self.store), key=KEY)
        parsed = load_document(json.dumps(document))
        self.assertEqual(parsed["digest"], document["digest"])
        self.assertTrue(verify_document(parsed, chain_heads(self.store), key=KEY).ok)
        with self.assertRaises(ValueError):
            load_document("[]")


if __name__ == "__main__":
    unittest.main()
