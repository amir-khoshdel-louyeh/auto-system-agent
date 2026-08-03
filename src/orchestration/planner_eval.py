"""Phase 6 item [63]: measured planner quality, not qualitative claims.

One call to :func:`evaluate` produces the evidence the proposal asks for:
per-intent precision/recall/F1 and macro-F1 over a labeled split, slot
accuracy, request latency (average and p95, measured in-process) and the
fallback rate — the share of requests the offline planner could not answer by
itself (clarification, or a model consultation).

The functions are pure over the dataset, so the numbers in
`docs/PLANNER_EVAL.md` can be regenerated at any time with the same command.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from storage.run_metrics import percentile

from .dataset import DATASET_VERSION, DatasetExample, load_examples
from .intents import INTENTS, SLOT_TYPES


class _Planner(Protocol):
    def plan(self, text: str) -> Any:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class IntentScore:
    """Precision/recall/F1 for one intent (0.0 when there is no data)."""

    intent: str
    precision: float
    recall: float
    f1: float
    support: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "support": self.support,
        }


@dataclass(frozen=True)
class PlannerMetrics:
    """Everything item [63] promises to measure."""

    version: str
    split: str
    total: int
    accuracy: float
    macro_f1: float
    slot_accuracy: float
    slot_labels: int
    avg_latency_ms: float
    p95_latency_ms: float
    fallback_rate: float
    llm_rate: float
    per_intent: dict[str, IntentScore] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "split": self.split,
            "total": self.total,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "slot_accuracy": self.slot_accuracy,
            "slot_labels": self.slot_labels,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "fallback_rate": self.fallback_rate,
            "llm_rate": self.llm_rate,
            "per_intent": {name: score.as_dict() for name, score in self.per_intent.items()},
        }


def _round(value: float) -> float:
    return round(value, 4)


def per_intent_scores(pairs: list[tuple[str, str]]) -> dict[str, IntentScore]:
    """Precision/recall/F1 per intent from (expected, predicted) pairs."""
    scores: dict[str, IntentScore] = {}
    for intent in INTENTS:
        true_positive = sum(1 for want, got in pairs if want == intent and got == intent)
        false_positive = sum(1 for want, got in pairs if want != intent and got == intent)
        false_negative = sum(1 for want, got in pairs if want == intent and got != intent)
        support = sum(1 for want, _ in pairs if want == intent)
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores[intent] = IntentScore(
            intent=intent,
            precision=_round(precision),
            recall=_round(recall),
            f1=_round(f1),
            support=support,
        )
    return scores


def macro_f1(scores: dict[str, IntentScore]) -> float:
    """Mean F1 over intents that actually appear in the gold labels."""
    present = [score.f1 for score in scores.values() if score.support]
    if not present:
        return 0.0
    return _round(sum(present) / len(present))


def slot_accuracy(examples: list[DatasetExample], plans: dict[str, Any]) -> tuple[float, int]:
    """Share of labeled slots the planner reproduced (case-insensitive)."""
    total = hit = 0
    for example in examples:
        plan = plans[example.text]
        for key, expected in example.slots.items():
            total += 1
            produced = plan.slots.get(key) if isinstance(plan.slots, dict) else None
            if isinstance(expected, list):
                ok = [str(item).lower() for item in (produced or [])] == [
                    str(item).lower() for item in expected
                ]
            else:
                ok = str(produced or "").lower() == str(expected).lower()
            hit += ok
    return (_round(hit / total) if total else 1.0), total


def evaluate(
    planner: _Planner,
    *,
    split: str = "test",
    version: str = DATASET_VERSION,
) -> PlannerMetrics:
    """Run the planner over one split and aggregate every metric."""
    examples = load_examples(version, split=split)
    predictions: list[tuple[str, str]] = []
    plans: dict[str, Any] = {}
    latencies: list[float] = []
    fallbacks = llm_calls = 0
    for example in examples:
        started = time.perf_counter()
        plan = planner.plan(example.text)
        latencies.append((time.perf_counter() - started) * 1000.0)
        plans[example.text] = plan
        predictions.append((example.intent, str(plan.intent)))
        if getattr(plan, "needs_clarification", False):
            fallbacks += 1
        if getattr(plan, "planner", "") == "llm":
            llm_calls += 1

    scores = per_intent_scores(predictions)
    correct = sum(1 for want, got in predictions if want == got)
    accuracy, labels = slot_accuracy(examples, plans)
    total = len(examples)
    return PlannerMetrics(
        version=version,
        split=split,
        total=total,
        accuracy=_round(correct / total) if total else 0.0,
        macro_f1=macro_f1(scores),
        slot_accuracy=accuracy,
        slot_labels=labels,
        avg_latency_ms=_round(sum(latencies) / len(latencies)) if latencies else 0.0,
        p95_latency_ms=percentile(latencies, 95),
        fallback_rate=_round(fallbacks / total) if total else 0.0,
        llm_rate=_round(llm_calls / total) if total else 0.0,
        per_intent=scores,
    )


def slot_vocabulary() -> dict[str, str]:
    """The frozen slot types (kept here for report generation)."""
    return dict(SLOT_TYPES)
