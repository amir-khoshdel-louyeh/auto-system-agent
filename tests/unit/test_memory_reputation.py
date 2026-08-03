"""Phase 7 item [69]: the explainable reputation formula (FR-15).

The formula is the proposal's: 0.7 success rate plus 0.3 normalized rating,
starting at 20 for a new plan. Boundaries matter more than decimals, so the
tests pin every one of them.
"""

import unittest

from memory.reputation import (
    RATING_MAX,
    START_REPUTATION,
    WEIGHT_RATING,
    WEIGHT_SUCCESS,
    ReputationState,
    is_publication_ready,
    normalized_rating,
    reputation_value,
    success_rate,
)


class FormulaTests(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(WEIGHT_SUCCESS + WEIGHT_RATING, 1.0)

    def test_new_plan_starts_quarantined_at_twenty(self):
        self.assertEqual(START_REPUTATION, 20.0)
        self.assertEqual(reputation_value(reuse_count=0, success_count=0), 20.0)

    def test_perfect_success_without_ratings(self):
        self.assertEqual(reputation_value(reuse_count=4, success_count=4), 70.0)

    def test_perfect_success_with_perfect_ratings(self):
        self.assertEqual(
            reputation_value(reuse_count=2, success_count=2, rating_sum=10.0, rating_count=2),
            100.0,
        )

    def test_half_success_with_no_ratings(self):
        self.assertEqual(reputation_value(reuse_count=2, success_count=1), 35.0)

    def test_ratings_carry_their_weight(self):
        # 4/4 reuses with a mean rating of 2.5 -> 70 + 0.3*100*0.5 = 85
        self.assertEqual(
            reputation_value(reuse_count=4, success_count=4, rating_sum=5.0, rating_count=2),
            85.0,
        )

    def test_zero_success_with_ratings(self):
        self.assertEqual(
            reputation_value(reuse_count=1, success_count=0, rating_sum=5.0, rating_count=1),
            30.0,
        )

    def test_scores_stay_inside_the_range(self):
        self.assertEqual(
            reputation_value(reuse_count=1, success_count=1, rating_sum=1000.0, rating_count=1),
            100.0,
        )
        self.assertGreaterEqual(reputation_value(reuse_count=9, success_count=0), 0.0)

    def test_helpers_handle_empty_inputs(self):
        self.assertEqual(success_rate(0, 0), 0.0)
        self.assertEqual(normalized_rating(0.0, 0), 0.0)
        self.assertEqual(normalized_rating(RATING_MAX, 1), 1.0)


class StateTests(unittest.TestCase):
    def test_fresh_state_is_the_start_value(self):
        self.assertEqual(ReputationState().reputation, 20.0)
        self.assertEqual(ReputationState().reuse_count, 0)

    def test_apply_counts_success_and_failure(self):
        state = ReputationState().apply(success=True).apply(success=False)
        self.assertEqual(state.reuse_count, 2)
        self.assertEqual(state.success_count, 1)
        self.assertEqual(state.reputation, 35.0)

    def test_apply_records_valid_ratings_only(self):
        state = ReputationState().apply(success=True, rating=5).apply(success=True, rating=9)
        self.assertEqual(state.rating_count, 1)
        self.assertEqual(state.rating_sum, 5.0)
        self.assertEqual(state.reputation, 100.0)

    def test_updates_are_monotonic_for_repeated_success(self):
        state = ReputationState()
        history = []
        for _ in range(3):
            state = state.apply(success=True, rating=5)
            history.append(state.reputation)
        self.assertEqual(history, sorted(history))
        self.assertEqual(history[-1], 100.0)

    def test_failures_lower_the_score(self):
        good = ReputationState().apply(success=True)
        worse = good.apply(success=False)
        self.assertLess(worse.reputation, good.reputation)

    def test_explanation_shows_the_derivation(self):
        text = ReputationState().apply(success=True).explain()
        self.assertIn("0.7", text)
        self.assertIn("0.3", text)
        self.assertIn("70.00", text)

    def test_publication_readiness_needs_proofs_and_reputation(self):
        self.assertTrue(is_publication_ready(60.0, 1))
        self.assertFalse(is_publication_ready(59.9, 1))
        self.assertFalse(is_publication_ready(100.0, 0))


if __name__ == "__main__":
    unittest.main()
