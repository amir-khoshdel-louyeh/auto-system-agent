"""Phase 7 item [66]: deterministic local embeddings.

The embedding must be reproducible across processes (SHA-256, not the
randomised builtin hash), unit-length so the dot product is a cosine, and
sensible: a shared-vocabulary plan ranks above an unrelated one.
"""

import json
import math
import subprocess
import sys
import unittest

from memory.embeddings import (
    DIM,
    EMBEDDING_MODEL,
    cosine,
    describe,
    deserialize,
    embed,
    serialize,
    similarity,
    tokenize,
)


class EmbedTests(unittest.TestCase):
    def test_dimension_and_identity_are_frozen(self):
        self.assertEqual(DIM, 256)
        self.assertEqual(EMBEDDING_MODEL, "hashing-v1")
        self.assertEqual(describe(), {"model": "hashing-v1", "dim": 256})
        self.assertEqual(len(embed("mkdir -p logs")), DIM)

    def test_embedding_is_deterministic(self):
        first = embed("create a workspace called demo-app")
        second = embed("create a workspace called demo-app")
        self.assertEqual(first, second)

    def test_embedding_is_stable_across_processes(self):
        code = (
            "import sys; sys.path.insert(0, 'src');"
            "from memory.embeddings import serialize, embed;"
            "print(serialize(embed('create a workspace called demo-app')))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(
            json.loads(out), json.loads(serialize(embed("create a workspace called demo-app")))
        )

    def test_vectors_are_unit_length(self):
        vector = embed("install firefox and vlc")
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in vector)), 1.0, places=6)

    def test_empty_text_is_a_zero_vector(self):
        vector = embed("")
        self.assertEqual(vector, [0.0] * DIM)
        self.assertEqual(cosine(vector, vector), 0.0)

    def test_different_plans_differ(self):
        self.assertLess(similarity("mkdir logs", "install firefox"), 0.99)

    def test_shared_vocabulary_is_more_similar(self):
        related = similarity(
            "create a workspace called demo-app with three folders",
            "create a workspace called demo-app with a README",
        )
        unrelated = similarity(
            "create a workspace called demo-app with three folders",
            "install firefox and print the disk usage",
        )
        self.assertGreater(related, unrelated)
        self.assertGreater(related, 0.5)

    def test_tokenize_keeps_paths_and_lowercases(self):
        self.assertEqual(tokenize("Mkdir -p Demo-App/logs"), ["mkdir", "-p", "demo-app/logs"])

    def test_cosine_guards_mismatched_shapes(self):
        self.assertEqual(cosine([1.0, 0.0], [1.0]), 0.0)
        self.assertEqual(cosine([], []), 0.0)


class SerializationTests(unittest.TestCase):
    def test_round_trip(self):
        vector = embed("write a README with the text hello team")
        restored = deserialize(serialize(vector))
        self.assertEqual(len(restored), DIM)
        self.assertAlmostEqual(cosine(vector, restored), 1.0, places=5)

    def test_garbage_yields_a_zero_vector(self):
        for payload in ("", "not json", "[]", json.dumps([1.0] * 3), json.dumps(["x"] * DIM)):
            with self.subTest(payload=payload[:20]):
                self.assertEqual(deserialize(payload), [0.0] * DIM)

    def test_serialized_form_is_compact(self):
        self.assertNotIn(" ", serialize(embed("mkdir logs")))


if __name__ == "__main__":
    unittest.main()
