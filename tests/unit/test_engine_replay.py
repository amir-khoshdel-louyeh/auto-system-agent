"""Phase 5 item [53]: normalized output comparison for replay (FR-13).

Comparison must not depend on key order or on volatile timing fields, must
tolerate the 500-character summary bound used at persistence time, and must
name every differing, missing or unexpected node instead of collapsing to a
generic `match: false`.
"""

import unittest

from engine.executor.replay import (
    MAX_SUMMARY,
    ReplayComparison,
    compare_outputs,
    normalize_output,
    normalize_summary,
)


class NormalizeOutputTests(unittest.TestCase):
    def test_key_order_is_canonical(self):
        self.assertEqual(normalize_output({"b": 2, "a": 1}), normalize_output({"a": 1, "b": 2}))

    def test_volatile_top_level_keys_are_dropped(self):
        self.assertEqual(
            normalize_output({"echo": "hi", "duration_ms": 12.5}),
            normalize_output({"echo": "hi"}),
        )

    def test_volatile_keys_are_dropped_at_depth(self):
        self.assertEqual(
            normalize_output({"outer": {"ts": 1, "timestamp": "now", "value": 3}}),
            normalize_output({"outer": {"value": 3}}),
        )

    def test_non_mapping_outputs_are_preserved(self):
        self.assertEqual(normalize_output(["a", "b"]), '["a", "b"]')
        self.assertEqual(normalize_output("done"), '"done"')

    def test_none_output_is_the_empty_object(self):
        self.assertEqual(normalize_output(None), "{}")

    def test_output_is_bounded(self):
        text = normalize_output({"blob": "x" * 5000})
        self.assertLessEqual(len(text), MAX_SUMMARY)


class NormalizeSummaryTests(unittest.TestCase):
    def test_json_summary_is_recanonicalised(self):
        self.assertEqual(normalize_summary('{"b": 2, "a": 1}'), '{"a": 1, "b": 2}')

    def test_volatile_keys_are_dropped_from_a_stored_summary(self):
        self.assertEqual(
            normalize_summary('{"echo": "hi", "ts": 10}'), normalize_summary('{"echo": "hi"}')
        )

    def test_plain_text_is_trimmed(self):
        self.assertEqual(normalize_summary("  permission denied \n"), "permission denied")

    def test_empty_summary_stays_empty(self):
        self.assertEqual(normalize_summary(""), "")
        self.assertEqual(normalize_summary(None), "")

    def test_truncated_stored_summary_matches_a_full_rerun(self):
        """The store keeps 500 characters; replay normalises to the same bound."""
        output = {"blob": "x" * 2000}
        stored = normalize_output(output)[:MAX_SUMMARY]
        self.assertEqual(normalize_summary(stored), normalize_output(output))
        self.assertTrue(compare_outputs({"a": stored}, {"a": normalize_output(output)}).match)


class CompareOutputsTests(unittest.TestCase):
    def test_identical_outputs_match(self):
        comparison = compare_outputs(
            {"a": '{"x": 1}', "b": '{"y": 2}'}, {"a": '{"x": 1}', "b": '{"y": 2}'}
        )
        self.assertTrue(comparison.match)
        self.assertEqual(comparison.compared, 2)
        self.assertEqual(comparison.mismatched, [])
        self.assertEqual(comparison.details, {})

    def test_key_order_and_timings_do_not_cause_false_mismatches(self):
        comparison = compare_outputs(
            {"a": '{"echo": "hi", "ts": 1}'}, {"a": '{"echo": "hi", "duration_ms": 9}'}
        )
        self.assertTrue(comparison.match)

    def test_mismatch_is_reported_by_node_with_both_sides(self):
        comparison = compare_outputs({"a": '{"x": 1}'}, {"a": '{"x": 2}'})
        self.assertFalse(comparison.match)
        self.assertEqual(comparison.mismatched, ["a"])
        self.assertEqual(comparison.details["a"], {"source": '{"x": 1}', "replay": '{"x": 2}'})

    def test_missing_and_unexpected_nodes_are_named(self):
        comparison = compare_outputs(
            {"a": '{"x": 1}', "b": '{"y": 2}'}, {"a": '{"x": 1}', "c": "{}"}
        )
        self.assertFalse(comparison.match)
        self.assertEqual(comparison.missing, ["b"])
        self.assertEqual(comparison.unexpected, ["c"])
        self.assertEqual(comparison.compared, 1)

    def test_empty_inputs_match(self):
        self.assertTrue(compare_outputs({}, {}).match)

    def test_as_dict_round_trip(self):
        body = compare_outputs({"a": "1"}, {"a": "2"}).as_dict()
        self.assertEqual(
            set(body), {"match", "compared", "mismatched", "missing", "unexpected", "details"}
        )
        self.assertIsInstance(compare_outputs({}, {}), ReplayComparison)


if __name__ == "__main__":
    unittest.main()
