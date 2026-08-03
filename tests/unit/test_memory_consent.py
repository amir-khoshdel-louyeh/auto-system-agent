"""Phase 7 item [64]: consent records and revocation (FR-14).

Consent is a record with a life cycle, not a boolean: granting is idempotent,
revoking keeps the row for audit, re-granting revives it, and a missing or
revoked consent makes the store refuse (fail closed) rather than degrade.
"""

import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from memory.consent import (
    SCOPE_MEMORY_STORE,
    SCOPES,
    Consent,
    ConsentError,
    ConsentStore,
)


class ConsentStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.store = ConsentStore(self.db)

    def test_grant_creates_a_live_consent(self):
        consent = self.store.grant("alice")
        self.assertIsInstance(consent, Consent)
        self.assertEqual(consent.subject, "alice")
        self.assertEqual(consent.scope, SCOPE_MEMORY_STORE)
        self.assertTrue(consent.active)
        self.assertTrue(self.store.is_granted("alice"))
        self.assertTrue(consent.granted_at)

    def test_grant_is_idempotent(self):
        first = self.store.grant("alice")
        second = self.store.grant("alice")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.store.list("alice")), 1)

    def test_missing_consent_is_not_granted(self):
        self.assertFalse(self.store.is_granted("bob"))
        with self.assertRaises(ConsentError):
            self.store.require("bob")

    def test_revocation_keeps_history_and_disables_reuse(self):
        consent = self.store.grant("alice")
        revoked = self.store.revoke("alice")
        self.assertEqual(revoked.id, consent.id)
        self.assertFalse(revoked.active)
        self.assertFalse(self.store.is_granted("alice"))
        with self.assertRaises(ConsentError):
            self.store.require("alice")
        # The record itself survives revocation (audit history).
        self.assertEqual(len(self.store.list("alice")), 1)

    def test_regrant_revives_the_same_record(self):
        original = self.store.grant("alice")
        self.store.revoke("alice")
        revived = self.store.grant("alice")
        self.assertEqual(revived.id, original.id)
        self.assertTrue(self.store.is_granted("alice"))

    def test_scopes_are_independent(self):
        self.store.grant("alice", SCOPE_MEMORY_STORE)
        self.assertTrue(self.store.is_granted("alice", "memory:store"))
        self.assertFalse(self.store.is_granted("alice", "memory:reuse"))

    def test_unknown_scope_is_rejected(self):
        self.assertEqual(SCOPES, ("memory:store", "memory:reuse"))
        with self.assertRaises(ConsentError):
            self.store.grant("alice", "memory:everything")
        # An unknown scope is a caller bug, so the check fails loudly rather
        # than answering False and hiding the mistake.
        with self.assertRaises(ConsentError):
            self.store.is_granted("alice", "memory:everything")

    def test_empty_subject_is_rejected(self):
        for subject in ("", "   "):
            with self.subTest(subject=subject):
                with self.assertRaises(ConsentError):
                    self.store.grant(subject)

    def test_revoking_an_unknown_consent_raises(self):
        with self.assertRaises(KeyError):
            self.store.revoke("ghost")

    def test_list_filters_by_subject(self):
        self.store.grant("alice")
        self.store.grant("bob")
        self.store.grant("alice", "memory:reuse")
        self.assertEqual(len(self.store.list("alice")), 2)
        self.assertEqual(len(self.store.list()), 3)
        self.assertEqual(
            [c.scope for c in self.store.list("alice")], ["memory:reuse", "memory:store"]
        )

    def test_consent_survives_a_new_store_instance(self):
        self.store.grant("alice")
        self.assertTrue(ConsentStore(self.db).is_granted("alice"))


if __name__ == "__main__":
    unittest.main()
