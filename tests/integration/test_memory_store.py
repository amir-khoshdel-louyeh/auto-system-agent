"""Phase 7 items [70], [71]: the memory store lifecycle (FR-14, FR-15).

A plan only reaches storage with live consent, arrives PII-scrubbed and
quarantined, is promoted only after proven sandbox runs, is reused through
the filters and can be erased on demand.
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from memory.consent import ConsentError, ConsentStore
from memory.reputation import START_REPUTATION
from memory.retrieval import RetrievalFilters, retrieve
from memory.states import (
    PUBLIC,
    QUARANTINED,
    SANDBOX,
    InvalidStateTransition,
    next_state,
)
from memory.store import MemoryError, MemoryStore

PLAN = {
    "intent": "create_workspace",
    "steps": [
        {"id": "create-demo-app", "type": "shell", "run": "mkdir -p demo-app"},
        {"id": "write-readme", "type": "shell", "run": "printf '# demo-app' > demo-app/README.md"},
    ],
}


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.consents = ConsentStore(self.db)
        self.store = MemoryStore(self.db, consents=self.consents)

    # -- consent gate ----------------------------------------------------
    def test_save_requires_consent(self):
        with self.assertRaises(ConsentError):
            self.store.save(PLAN, subject="alice")
        self.assertEqual(self.store.count(), 0)

    def test_save_after_consent_starts_quarantined(self):
        self.consents.grant("alice")
        item = self.store.save(
            PLAN, subject="alice", summary="create demo-app", intent="create_workspace"
        )
        self.assertEqual(item.state, QUARANTINED)
        self.assertEqual(item.reputation, START_REPUTATION)
        self.assertFalse(item.reusable)
        self.assertEqual(item.reuse_count, 0)

    def test_revoked_consent_blocks_new_writes(self):
        self.consents.grant("alice")
        self.store.save(PLAN, subject="alice")
        self.consents.revoke("alice")
        with self.assertRaises(ConsentError):
            self.store.save({"intent": "other"}, subject="alice")

    # -- scrubbing -------------------------------------------------------
    def test_pii_never_reaches_the_stored_plan(self):
        self.consents.grant("alice")
        item = self.store.save(
            {"intent": "run_command", "steps": [{"run": "mail alice@example.com"}]},
            subject="alice",
        )
        self.assertNotIn("alice@example.com", item.canonical_plan)
        self.assertGreater(item.pii_findings, 0)

    def test_missing_consent_means_no_embedding_written(self):
        with self.assertRaises(ConsentError):
            self.store.save(PLAN, subject="alice")
        self.assertEqual(self.store.list_items(), [])

    def test_store_is_idempotent_per_fingerprint(self):
        self.consents.grant("alice")
        first = self.store.save(PLAN, subject="alice")
        second = self.store.save(PLAN, subject="alice", summary="again")
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(second.summary, first.summary)

    def test_rejects_unknown_risk_and_empty_plan(self):
        self.consents.grant("alice")
        with self.assertRaises(MemoryError):
            self.store.save(PLAN, subject="alice", risk="CATASTROPHIC")
        with self.assertRaises(MemoryError):
            self.store.save({}, subject="alice")

    # -- lifecycle -------------------------------------------------------
    def test_successful_sandbox_run_promotes_to_sandbox(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice")
        promoted = self.store.record_episode(item.id, success=True)
        self.assertEqual(promoted.state, SANDBOX)
        self.assertEqual(promoted.reuse_count, 1)
        self.assertEqual(promoted.success_count, 1)

    def test_failed_run_stays_quarantined_and_lowers_reputation(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice")
        failed = self.store.record_episode(item.id, success=False)
        self.assertEqual(failed.state, QUARANTINED)
        self.assertLess(failed.reputation, START_REPUTATION)
        self.assertFalse(failed.reusable)

    def test_sandbox_item_becomes_public_only_with_reputation(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice")
        sandboxed = self.store.record_episode(item.id, success=True)
        self.assertEqual(sandboxed.state, SANDBOX)
        published = self.store.record_episode(item.id, success=True, rating=5)
        self.assertEqual(published.state, PUBLIC)
        self.assertTrue(published.reusable)

    def test_public_item_that_fails_is_quarantined_again(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice")
        self.store.record_episode(item.id, success=True)
        self.store.record_episode(item.id, success=True, rating=5)
        poisoned = self.store.record_episode(item.id, success=False)
        self.assertEqual(poisoned.state, QUARANTINED)
        self.assertFalse(poisoned.reusable)

    def test_illegal_state_jumps_are_rejected_by_the_lifecycle(self):
        with self.assertRaises(InvalidStateTransition):
            from memory.states import require_transition

            require_transition(QUARANTINED, PUBLIC)

    def test_next_state_keeps_failures_in_place(self):
        self.assertEqual(
            next_state(QUARANTINED, sandbox_proofs=0, reputation=100.0, last_outcome_ok=False),
            QUARANTINED,
        )
        self.assertEqual(
            next_state(SANDBOX, sandbox_proofs=0, reputation=100.0, last_outcome_ok=True), SANDBOX
        )

    # -- retrieval and erasure -------------------------------------------
    def test_quarantined_plan_is_not_retrievable_until_public(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice", summary="create demo-app workspace")
        self.assertFalse(retrieve("create demo-app workspace", [item]).hit)
        published = self.store.list_items(state=PUBLIC)
        self.assertEqual(published, [])
        self.store.record_episode(item.id, success=True)
        self.store.record_episode(item.id, success=True, rating=5)
        hits = retrieve(
            "create demo-app workspace",
            self.store.list_items(),
            filters=RetrievalFilters(states=(PUBLIC,)),
        )
        self.assertTrue(hits.hit)
        self.assertEqual(hits.hits[0].id, item.id)

    def test_erasure_removes_item_and_events(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice")
        self.store.record_episode(item.id, success=True)
        self.assertTrue(self.store.events(item.id))
        self.assertTrue(self.store.erase(item.id))
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(self.store.events(item.id), [])
        self.assertFalse(self.store.erase(item.id), "second erase reports not-found")
        with self.assertRaises(KeyError):
            self.store.get(item.id)

    def test_erasure_keeps_the_consent_history(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice")
        self.store.erase(item.id)
        consents = self.consents.list("alice")
        self.assertEqual(len(consents), 1)
        self.assertTrue(consents[0].active)

    def test_item_serialises_for_the_api(self):
        self.consents.grant("alice")
        item = self.store.save(PLAN, subject="alice")
        body = item.as_dict()
        self.assertEqual(body["state"], QUARANTINED)
        self.assertFalse(body["reusable"])
        self.assertIsInstance(json.dumps(body), str)
        self.assertEqual(item.plan()["intent"], "create_workspace")


if __name__ == "__main__":
    unittest.main()
