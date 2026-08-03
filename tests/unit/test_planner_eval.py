"""Phase 6 item [63]: measured planner quality on labeled data.

Precision, recall, F1 (per intent and macro), slot accuracy, latency and the
fallback rate are computed from labeled splits, with regression floors so a
rule change that lowers quality fails the suite instead of shipping.
"""

import json
import unittest

from orchestration.dataset import load_examples
from orchestration.llm_adapter import LLMPlanningAdapter, ModelError
from orchestration.offline_planner import OfflinePlanner, PlannerConfig
from orchestration.planner_eval import (
    IntentScore,
    PlannerMetrics,
    evaluate,
    macro_f1,
    per_intent_scores,
    slot_accuracy,
)
from orchestration.planner_service import PlannerService


class FakeClient:
    provider = "fake"
    model = "fake-1"

    def complete(self, prompt: str, *, timeout_s: float) -> str:
        raise ModelError("offline in tests")


class PerIntentScoreTests(unittest.TestCase):
    def test_perfect_predictions_score_one(self):
        pairs = [("install_app", "install_app"), ("help", "help")]
        scores = per_intent_scores(pairs)
        self.assertEqual(scores["install_app"].f1, 1.0)
        self.assertEqual(scores["install_app"].support, 1)
        self.assertEqual(scores["create_folder"].support, 0)
        self.assertEqual(macro_f1(scores), 1.0)

    def test_precision_recall_and_f1_are_computed_per_intent(self):
        # Two gold install_app: one right, one predicted create_folder;
        # one gold create_folder predicted install_app.
        pairs = [
            ("install_app", "install_app"),
            ("install_app", "create_folder"),
            ("create_folder", "install_app"),
        ]
        install = per_intent_scores(pairs)["install_app"]
        self.assertAlmostEqual(install.precision, 1 / 2)
        self.assertAlmostEqual(install.recall, 1 / 2)
        self.assertAlmostEqual(install.f1, 1 / 2)
        folder = per_intent_scores(pairs)["create_folder"]
        self.assertEqual(folder.precision, 0.0)
        self.assertEqual(folder.support, 1)

    def test_macro_f1_ignores_absent_intents(self):
        scores = {
            "a": IntentScore("a", 1.0, 1.0, 1.0, 1),
            "b": IntentScore("b", 0.0, 0.0, 0.0, 0),
        }
        self.assertEqual(macro_f1(scores), 1.0)
        self.assertEqual(macro_f1({}), 0.0)


class SlotAccuracyTests(unittest.TestCase):
    class _Plan:
        def __init__(self, slots):
            self.slots = slots

    def test_slots_are_compared_case_insensitively(self):
        example = load_examples()[0]
        plan = self._Plan(
            {"folder_names": [name.upper() for name in example.slots["folder_names"]]}
        )
        accuracy, labels = slot_accuracy([example], {example.text: plan})
        self.assertEqual(accuracy, 1.0)
        self.assertEqual(labels, len(example.slots))

    def test_missing_slots_lower_the_score(self):
        example = load_examples()[0]
        accuracy, _ = slot_accuracy([example], {example.text: self._Plan({})})
        self.assertEqual(accuracy, 0.0)


class EvaluateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.offline = evaluate(OfflinePlanner(), split="test")

    def test_offline_planner_meets_the_quality_floors(self):
        self.assertGreaterEqual(self.offline.accuracy, 0.95)
        self.assertGreaterEqual(self.offline.macro_f1, 0.85)
        self.assertGreaterEqual(self.offline.slot_accuracy, 0.85)
        self.assertLessEqual(self.offline.fallback_rate, 0.25)

    def test_latency_is_measured_and_local(self):
        self.assertGreater(self.offline.avg_latency_ms, 0.0)
        self.assertLess(self.offline.avg_latency_ms, 20.0, "offline planner must stay under 20 ms")
        self.assertGreaterEqual(self.offline.p95_latency_ms, self.offline.avg_latency_ms / 2)

    def test_counts_add_up(self):
        self.assertEqual(self.offline.total, len(load_examples(split="test")))
        self.assertEqual(
            sum(score.support for score in self.offline.per_intent.values()),
            self.offline.total,
        )
        self.assertEqual(self.offline.version, "intents-v1")
        self.assertEqual(self.offline.split, "test")

    def test_fallback_rate_tracks_clarification(self):
        planner = OfflinePlanner(PlannerConfig(confidence_threshold=1.0))
        metrics = evaluate(planner, split="test")
        # Every request except the exact-match help question now needs help.
        self.assertGreaterEqual(metrics.fallback_rate, 0.9)
        self.assertLess(metrics.fallback_rate, 1.0)
        self.assertEqual(
            metrics.accuracy, 1.0, "the intent is still classified; only trust changes"
        )

    def test_llm_rate_counts_model_consultations(self):
        service = PlannerService(adapter=LLMPlanningAdapter(FakeClient()))
        metrics = evaluate(service, split="test")
        self.assertEqual(
            metrics.llm_rate, 0.0, "a dead provider leaves the offline answer in place"
        )
        self.assertGreaterEqual(metrics.macro_f1, 0.85)

    def test_train_split_is_also_measurable(self):
        metrics = evaluate(OfflinePlanner(), split="train")
        self.assertGreaterEqual(metrics.accuracy, 0.95)
        self.assertEqual(metrics.total, len(load_examples(split="train")))

    def test_metrics_serialise_for_the_report(self):
        body = self.offline.as_dict()
        self.assertEqual(
            set(body),
            {
                "version",
                "split",
                "total",
                "accuracy",
                "macro_f1",
                "slot_accuracy",
                "slot_labels",
                "avg_latency_ms",
                "p95_latency_ms",
                "fallback_rate",
                "llm_rate",
                "per_intent",
            },
        )
        self.assertIsInstance(json.dumps(body), str)
        self.assertEqual(
            set(body["per_intent"]["install_app"]),
            {"intent", "precision", "recall", "f1", "support"},
        )
        self.assertIsInstance(self.offline, PlannerMetrics)


if __name__ == "__main__":
    unittest.main()
