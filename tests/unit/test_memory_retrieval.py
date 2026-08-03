"""Phase 7 item [67]: similarity retrieval and top-k ranking.

Ranking is deterministic and explainable: the best cosine score wins, ties
break on reputation and then on id, and the query that produced the hits is
returned with them for the audit trail.
"""

import unittest
from dataclasses import dataclass

from memory.embeddings import embed, serialize
from memory.retrieval import (
    DEFAULT_TOP_K,
    RetrievalResult,
    RetrievedItem,
    rank,
    score_item,
)


@dataclass
class Item:
    """Minimal memory-like row for ranking tests."""

    id: str
    text: str
    reputation: float = 20.0
    state: str = "PUBLIC"
    intent: str = "create_folder"

    @property
    def embedding(self) -> str:
        return serialize(embed(self.text))


def items(*specs) -> list[Item]:
    return [Item(*spec) for spec in specs]


class RankTests(unittest.TestCase):
    def setUp(self):
        self.corpus = items(
            ("a", "create a workspace called demo-app with three folders and a README", 40.0),
            ("b", "install firefox", 20.0),
            ("c", "create a workspace called demo-app with three folders", 30.0),
            ("d", "list the files in ~/Downloads", 90.0),
        )

    def test_most_similar_item_ranks_first(self):
        result = rank(
            "create a workspace called demo-app with three folders and a README", self.corpus
        )
        self.assertIsInstance(result, RetrievalResult)
        self.assertTrue(result.hit)
        self.assertEqual(result.best.id, "a")
        self.assertGreater(result.hits[0].score, result.hits[-1].score)

    def test_top_k_limits_and_ranks_sequentially(self):
        # The exact-substring plan is the closest match to the shorter query.
        result = rank("create a workspace called demo-app with three folders", self.corpus, top_k=2)
        self.assertEqual(len(result.hits), 2)
        self.assertEqual([hit.rank for hit in result.hits], [1, 2])
        self.assertEqual(result.hits[0].item.id, "c")

    def test_default_top_k_is_five(self):
        self.assertEqual(DEFAULT_TOP_K, 5)
        self.assertEqual(len(rank("install firefox", self.corpus).hits), 1)
        self.assertLessEqual(len(rank("create a workspace called demo-app", self.corpus).hits), 5)

    def test_ties_break_on_reputation_then_id(self):
        twin_a = Item("a", "install firefox", 10.0)
        twin_b = Item("b", "install firefox", 50.0)
        twin_c = Item("c", "install firefox", 50.0)
        result = rank("install firefox", [twin_a, twin_c, twin_b])
        self.assertEqual([hit.item.id for hit in result.hits], ["b", "c", "a"])

    def test_min_score_drops_unrelated_candidates(self):
        strict = rank("install firefox", self.corpus, min_score=0.2)
        self.assertTrue(all(hit.score > 0.2 for hit in strict.hits))
        self.assertLessEqual(len(strict.hits), len(self.corpus))

    def test_empty_corpus_returns_no_hits(self):
        result = rank("anything", [])
        self.assertFalse(result.hit)
        self.assertIsNone(result.best)
        self.assertEqual(
            result.as_dict(),
            {
                "query": "anything",
                "considered": 0,
                "filtered": 0,
                "rejections": {},
                "hits": [],
            },
        )

    def test_identical_text_scores_one(self):
        result = rank("install firefox", [Item("x", "install firefox")])
        self.assertAlmostEqual(result.hits[0].score, 1.0, places=5)

    def test_unrelated_items_are_not_returned(self):
        result = rank("install firefox", [Item("z", "mkdir logs")])
        self.assertFalse(result.hit)

    def test_ranking_is_repeatable(self):
        first = rank("mkdir logs", self.corpus, top_k=3)
        second = rank("mkdir logs", self.corpus, top_k=3)
        self.assertEqual([hit.id for hit in first.hits], [hit.id for hit in second.hits])

    def test_top_k_must_be_positive(self):
        with self.assertRaises(ValueError):
            rank("mkdir logs", self.corpus, top_k=0)

    def test_score_item_accepts_a_vector_directly(self):
        item = Item("x", "install firefox")
        self.assertAlmostEqual(score_item(embed("install firefox"), item), 1.0, places=5)

    def test_hit_serialises_for_the_api(self):
        hit = rank("install firefox", self.corpus).hits[0]
        self.assertIsInstance(hit, RetrievedItem)
        self.assertEqual(
            set(hit.as_dict()), {"id", "score", "rank", "reputation", "state", "intent"}
        )


if __name__ == "__main__":
    unittest.main()
