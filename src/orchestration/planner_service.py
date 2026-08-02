"""Phase 6 item [62]: planner service with provider metadata, no secrets.

The offline planner is always the first and final authority: it decides
whether a request is understood. Only when it asks for clarification (a
low-confidence match, a missing slot, or no rule at all) *may* the optional
LLM adapter be consulted for a typed suggestion. Whatever comes back is run
through the same trust rules (:meth:`OfflinePlanner.from_slots`) and the same
validator/policy pipeline, so the model cannot relax any check.

Every consultation is recorded as provider metadata (provider, model,
attempts, outcome, latency and whether a fallback answered) on the plan.
Only those scalars are stored — never an API key, endpoint or raw payload
(item [62]) — and a plan whose model call failed simply keeps the offline
clarification.
"""

from __future__ import annotations

from dataclasses import replace

from .intents import Plan
from .llm_adapter import LLMPlanningAdapter
from .offline_planner import OfflinePlanner, PlannerConfig

#: Confidence assigned to a schema-valid model suggestion (threshold still applies).
LLM_CONFIDENCE = 0.85


class PlannerService:
    """Offline-first planner with an optional, typed LLM fallback."""

    def __init__(
        self,
        *,
        config: PlannerConfig | None = None,
        adapter: LLMPlanningAdapter | None = None,
        offline: OfflinePlanner | None = None,
    ) -> None:
        self.offline = offline or OfflinePlanner(config)
        self.adapter = adapter

    def plan(self, text: str) -> Plan:
        """Typed plan for ``text``; consults the model only when needed."""
        offline_plan = self.offline.plan(text)
        if self.adapter is None or not self._may_ask_llm(offline_plan):
            return offline_plan

        suggestion = self.adapter.suggest(text)
        metadata = {"llm": self.adapter.metadata().as_dict()}
        if suggestion is None:
            return replace(offline_plan, metadata=metadata)
        return self.offline.from_slots(
            suggestion.intent,
            suggestion.slots,
            confidence=LLM_CONFIDENCE,
            raw_input=text,
            planner="llm",
            metadata=metadata,
        )

    @staticmethod
    def _may_ask_llm(plan: Plan) -> bool:
        """A model is only consulted when the offline answer is unusable."""
        if plan.intent == "help":
            return False
        return plan.needs_clarification
