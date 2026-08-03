"""Phase 7 item [68]: filter reuse by risk, OS compatibility and quarantine.

Filtering happens before scoring, so a quarantined, high-risk or off-platform
plan can never be presented as a reuse candidate however similar it looks.
"""

import unittest
from dataclasses import dataclass

from memory.embeddings import embed, serialize
from memory.retrieval import (
    RISK_ORDER,
    RetrievalFilters,
    retrieve,
)
from memory.states import PUBLIC, QUARANTINED, SANDBOX


@dataclass
class Item:
    id: str
    text: str = "create a workspace called demo-app"
    state: str = PUBLIC
    risk: str = "LOW"
    os_name: str = "linux"
    reputation: float = 70.0
    intent: str = "create_workspace"

    @property
    def embedding(self) -> str:
        return serialize(embed(self.text))


class FilterDefaultsTests(unittest.TestCase):
    def test_defaults_are_safe(self):
        filters = RetrievalFilters()
        self.assertEqual(filters.states, (PUBLIC,))
        self.assertEqual(filters.max_risk, "MEDIUM")
        self.assertEqual(filters.os_name, "")
        self.assertEqual(filters.min_reputation, 0.0)
        self.assertTrue(filters.accepts(Item("ok")))
        self.assertEqual(filters.rejects(Item("ok")), "")

    def test_risk_order_is_frozen(self):
        self.assertEqual(RISK_ORDER, {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "UNSAFE": 3})


class FilterTests(unittest.TestCase):
    def test_quarantined_and_sandboxed_items_are_not_reusable(self):
        filters = RetrievalFilters()
        for state in (QUARANTINED, SANDBOX):
            with self.subTest(state=state):
                reason = filters.rejects(Item("x", state=state))
                self.assertIn("state", reason)

    def test_unknown_state_is_rejected(self):
        self.assertIn("state", RetrievalFilters().rejects(Item("x", state="MYSTERY")))

    def test_public_state_can_be_requested_explicitly(self):
        filters = RetrievalFilters(states=(SANDBOX,))
        self.assertTrue(filters.accepts(Item("x", state=SANDBOX)))
        self.assertIn("state", filters.rejects(Item("y", state=PUBLIC)))

    def test_risk_ceiling(self):
        self.assertTrue(RetrievalFilters(max_risk="HIGH").accepts(Item("x", risk="HIGH")))
        self.assertIn("risk", RetrievalFilters().rejects(Item("x", risk="HIGH")))
        self.assertIn("risk", RetrievalFilters().rejects(Item("x", risk="UNSAFE")))

    def test_os_compatibility(self):
        filters = RetrievalFilters(os_name="linux")
        self.assertTrue(filters.accepts(Item("x", os_name="linux")))
        self.assertIn("os", filters.rejects(Item("y", os_name="darwin")))
        # A plan with no recorded OS matches any host (no false rejection).
        self.assertTrue(filters.accepts(Item("z", os_name="")))

    def test_reputation_floor(self):
        filters = RetrievalFilters(min_reputation=60.0)
        self.assertTrue(filters.accepts(Item("x", reputation=60.0)))
        self.assertIn("reputation", filters.rejects(Item("y", reputation=20.0)))

    def test_intent_allow_list_and_exclusions(self):
        filters = RetrievalFilters(allowed_intents=("create_workspace",), exclude_ids=("banned",))
        self.assertTrue(filters.accepts(Item("ok")))
        self.assertIn("intent", filters.rejects(Item("other", intent="install_app")))
        self.assertIn("excluded", filters.rejects(Item("banned")))

    def test_filters_serialise(self):
        body = RetrievalFilters().as_dict()
        self.assertEqual(
            set(body),
            {"states", "max_risk", "os_name", "min_reputation", "allowed_intents", "exclude_ids"},
        )


class RetrieveTests(unittest.TestCase):
    def setUp(self):
        self.corpus = [
            Item("public", state=PUBLIC, reputation=80.0),
            Item("quarantined", state=QUARANTINED),
            Item("risky", risk="HIGH"),
            Item("windows", os_name="windows"),
        ]

    def test_retrieve_drops_unreusable_candidates_before_scoring(self):
        # An empty os_name means "any platform"; the reuse service passes the
        # host when it cares, so the windows item survives the default filters.
        result = retrieve("create a workspace called demo-app", self.corpus)
        self.assertEqual([hit.id for hit in result.hits], ["public", "windows"])
        self.assertEqual(result.considered, 4)
        self.assertEqual(result.filtered, 2)
        self.assertEqual(sum(result.rejections.values()), 2)

    def test_rejection_counts_are_grouped_by_reason(self):
        result = retrieve("create a workspace called demo-app", self.corpus)
        self.assertEqual(set(result.rejections), {"state", "risk"})

    def test_widening_the_filters_returns_more_candidates(self):
        wide = RetrievalFilters(
            states=(PUBLIC, SANDBOX, QUARANTINED), max_risk="HIGH", os_name="linux"
        )
        result = retrieve("create a workspace called demo-app", self.corpus, filters=wide)
        ids = [hit.id for hit in result.hits]
        self.assertIn("quarantined", ids)
        self.assertIn("risky", ids)
        self.assertNotIn("windows", ids, "OS compatibility still applies")

    def test_os_filter_matches_the_host(self):
        result = retrieve(
            "create a workspace called demo-app",
            self.corpus,
            filters=RetrievalFilters(os_name="windows"),
        )
        self.assertEqual([hit.id for hit in result.hits], ["windows"])

    def test_retrieve_result_serialises_filters_and_hits(self):
        body = retrieve("create a workspace called demo-app", self.corpus).as_dict()
        self.assertEqual(set(body), {"query", "considered", "filtered", "rejections", "hits"})
        self.assertEqual(body["hits"][0]["id"], "public")

    def test_no_candidate_survives_a_strict_filter(self):
        result = retrieve(
            "create a workspace called demo-app",
            self.corpus,
            filters=RetrievalFilters(min_reputation=99.0),
        )
        self.assertFalse(result.hit)
        self.assertEqual(result.filtered, 4)


if __name__ == "__main__":
    unittest.main()
