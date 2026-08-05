"""Phase 9 item [87]: the SUS instrument scores what it claims to.

The arithmetic is checked against the known anchors of the instrument (all
neutral answers score 50, the best case scores 100, the worst 0, a single
mis-keyed item moves the score by 2.5) and against an invalid study, because
a usability number is only worth quoting if the tool that produced it is
right.
"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.sus_score import (
    SusError,
    interpret,
    load_responses,
    main,
    participant_score,
    summarise,
    template,
)


class ScoringTests(unittest.TestCase):
    def test_all_neutral_answers_score_fifty(self):
        self.assertEqual(participant_score([3] * 10), 50.0)

    def test_the_best_possible_answers_score_one_hundred(self):
        self.assertEqual(participant_score([5, 1] * 5), 100.0)

    def test_the_worst_possible_answers_score_zero(self):
        self.assertEqual(participant_score([1, 5] * 5), 0.0)

    def test_a_single_mis_keyed_item_moves_the_score_by_2_5(self):
        base = participant_score([4, 2] * 5)
        changed = list([4, 2] * 5)
        changed[0] = 3
        self.assertEqual(base - participant_score(changed), 2.5)

    def test_positive_and_negative_items_are_keyed_oppositely(self):
        # Raising a positive item helps; raising a negative item hurts.
        self.assertGreater(participant_score([5, 2] * 5), participant_score([4, 2] * 5))
        self.assertLess(participant_score([4, 5] * 5), participant_score([4, 2] * 5))

    def test_out_of_range_and_malformed_responses_are_refused(self):
        for bad in ([6] * 10, [0] * 10, [3] * 9, [3] * 11, ["x"] + [3] * 9):
            with self.subTest(bad=bad):
                with self.assertRaises(SusError):
                    participant_score(bad)


class SummaryTests(unittest.TestCase):
    def test_spread_is_reported_next_to_the_mean(self):
        summary = summarise([[5, 1] * 5, [3, 3] * 5, [1, 5] * 5])
        self.assertEqual(summary["participants"], 3)
        self.assertEqual(summary["scores"], [100.0, 50.0, 0.0])
        self.assertEqual(summary["mean"], 50.0)
        self.assertEqual(summary["median"], 50.0)
        self.assertEqual(summary["min"], 0.0)
        self.assertEqual(summary["max"], 100.0)
        self.assertGreater(summary["stdev"], 0)
        self.assertFalse(summary["meets_target"])

    def test_a_strong_study_meets_the_target(self):
        summary = summarise([[5, 1] * 5, [5, 1] * 5, [4, 2] * 5])
        self.assertTrue(summary["meets_target"])
        self.assertEqual(summary["interpretation"], "A — users like it")

    def test_a_single_participant_has_no_spread(self):
        summary = summarise([[4, 2] * 5])
        self.assertEqual(summary["stdev"], 0.0)

    def test_an_empty_study_is_refused(self):
        with self.assertRaises(SusError):
            summarise([])

    def test_interpretation_bands(self):
        self.assertEqual(interpret(85.0), "A — users like it")
        self.assertEqual(interpret(70.0), "above average")
        self.assertEqual(interpret(40.0), "below average")


class FileAndCliTests(unittest.TestCase):
    def write(self, payload) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "responses.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_reads_a_json_study(self):
        participants = load_responses(self.write([[5, 1] * 5, [3] * 10]))
        self.assertEqual(len(participants), 2)

    def test_load_refuses_a_missing_or_malformed_file(self):
        with self.assertRaises(SusError):
            load_responses("/nonexistent/responses.json")
        with self.assertRaises(SusError):
            load_responses(self.write({"not": "a list"}))
        with self.assertRaises(SusError):
            load_responses(self.write([5, 1, 4, 2, 5, 1, 4, 2, 5, 1]))

    def test_cli_exit_codes_follow_the_target(self):
        self.assertEqual(main([str(self.write([[5, 1] * 5] * 5))]), 0)
        self.assertEqual(main([str(self.write([[3] * 10] * 5))]), 1)
        self.assertEqual(main(["/nonexistent/responses.json"]), 2)

    def test_template_documents_the_alternating_items(self):
        text = template()
        self.assertIn("1. I thought the system was easy to use.", text)
        self.assertIn("2. I found the system unnecessarily complex.", text)
        self.assertEqual(text.count("# positive"), 5)
        self.assertEqual(text.count("# negative"), 5)


if __name__ == "__main__":
    unittest.main()
