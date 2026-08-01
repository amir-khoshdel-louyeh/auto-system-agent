"""Phase 6 item [56]: versioned, validated labeled dataset.

Every line is checked against the frozen intent/slot vocabulary, duplicates
are rejected, and both splits are non-trivial so item [63] can measure on
held-out data.
"""

import json
import tempfile
import unittest
from pathlib import Path

from orchestration import dataset
from orchestration.dataset import (
    DATASET_VERSION,
    DatasetError,
    counts,
    dataset_path,
    load_examples,
    summary,
)
from orchestration.intents import EXECUTABLE_INTENTS, INTENTS, SLOT_TYPES


class DatasetFileTests(unittest.TestCase):
    def test_version_constant_matches_the_file(self):
        self.assertEqual(DATASET_VERSION, "intents-v1")
        self.assertTrue(dataset_path().is_file())

    def test_every_line_parses_within_the_vocabulary(self):
        examples = load_examples()
        self.assertGreaterEqual(len(examples), 80)
        for example in examples:
            with self.subTest(text=example.text):
                self.assertIn(example.intent, INTENTS)
                self.assertTrue(set(example.slots) <= set(SLOT_TYPES))
                self.assertIn(example.split, ("train", "test"))

    def test_test_split_is_held_out_and_covers_every_intent(self):
        test = load_examples(split="test")
        self.assertGreaterEqual(len(test), 15)
        covered = {example.intent for example in test}
        self.assertEqual(covered, set(INTENTS))
        self.assertTrue(set(EXECUTABLE_INTENTS) <= covered)

    def test_every_intent_has_examples(self):
        tally = counts(load_examples())
        for intent in INTENTS:
            with self.subTest(intent=intent):
                self.assertGreaterEqual(tally[intent], 8 if intent in EXECUTABLE_INTENTS else 8)

    def test_slots_are_only_labeled_where_the_intent_declares_them(self):
        from orchestration.intents import spec_for

        for example in load_examples():
            declares = set(spec_for(example.intent).required_slots) | set(
                spec_for(example.intent).optional_slots
            )
            with self.subTest(text=example.text):
                self.assertTrue(set(example.slots) <= declares)

    def test_summary_reports_counts(self):
        report = summary()
        self.assertEqual(report["version"], DATASET_VERSION)
        self.assertEqual(report["total"], len(load_examples()))
        self.assertEqual(report["splits"]["train"] + report["splits"]["test"], report["total"])
        self.assertEqual(
            report["intents"]["install_app"],
            len([e for e in load_examples() if e.intent == "install_app"]),
        )


class DatasetValidationTests(unittest.TestCase):
    def _with_lines(self, *lines: str):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "intents-vtest.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return directory.name

    def _load(self, *lines):
        directory = self._with_lines(*lines)
        original = dataset.DATA_DIR
        dataset.DATA_DIR = Path(directory)
        self.addCleanup(setattr, dataset, "DATA_DIR", original)
        return load_examples("intents-vtest")

    def test_unknown_intent_is_rejected(self):
        with self.assertRaises(DatasetError):
            self._load(json.dumps({"text": "do a barrel roll", "intent": "teleport"}))

    def test_unknown_slot_is_rejected(self):
        with self.assertRaises(DatasetError):
            self._load(
                json.dumps({"text": "install vlc", "intent": "install_app", "slots": {"vibe": 1}})
            )

    def test_duplicate_utterance_is_rejected(self):
        line = json.dumps({"text": "install vlc", "intent": "install_app"})
        with self.assertRaises(DatasetError):
            self._load(line, line)

    def test_bad_json_and_missing_text_are_rejected(self):
        with self.assertRaises(DatasetError):
            self._load("{not json")
        with self.assertRaises(DatasetError):
            self._load(json.dumps({"intent": "help"}))

    def test_unknown_split_is_rejected(self):
        with self.assertRaises(DatasetError):
            self._load(json.dumps({"text": "help", "intent": "help", "split": "holdout"}))
        with self.assertRaises(ValueError):
            load_examples(split="holdout")

    def test_unknown_version_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_examples("intents-v99")


if __name__ == "__main__":
    unittest.main()
