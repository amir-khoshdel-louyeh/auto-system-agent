"""Phase 6 item [60]: a typed LLM adapter behind an explicit interface.

The model is an *optional* proposal source, never an authority:

- it is reached through :class:`ModelClient` (a protocol), so the adapter has
  no transport of its own and tests need no network;
- its answer must be strict JSON validated against the frozen intent/slot
  vocabulary — an unknown intent, an undeclared slot or a non-JSON reply is
  rejected (:func:`parse_suggestion` returns ``None``);
- what it produces is a *suggestion*; the planner turns it into a typed plan
  that still goes through the same validator and policy gate as any other
  plan (FR-06).

Timeout, retry, provider fallback and metadata recording are layered on in
items [61] and [62].
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .intents import INTENT_SPECS, INTENTS, spec_for

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class ModelError(RuntimeError):
    """The model provider failed (transport, HTTP status, empty reply)."""


class ModelTimeout(ModelError):
    """The model provider did not answer inside the configured timeout."""


@runtime_checkable
class ModelClient(Protocol):
    """The only way the adapter talks to a model provider."""

    provider: str
    model: str

    def complete(self, prompt: str, *, timeout_s: float) -> str:
        """Return the model's raw text answer; raise ModelTimeout/ModelError."""
        ...  # pragma: no cover - protocol


@dataclass(frozen=True)
class LLMSuggestion:
    """A schema-valid proposal from a model (no metadata yet: item [62])."""

    intent: str
    slots: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


def prompt_for(text: str) -> str:
    """The classification prompt: frozen vocabulary, strict JSON answer."""
    catalogue = "\n".join(
        f"- {name}: required slots {list(INTENT_SPECS[name].required_slots)}"
        for name in INTENTS
        if name in INTENT_SPECS
    )
    return (
        "You classify one desktop-automation request into a fixed vocabulary.\n"
        f"Intents and their required slots:\n{catalogue}\n"
        "Answer with strict JSON only, no prose and no code fences:\n"
        '{"intent": "<intent>", "slots": {"<slot>": "<value>"}}\n'
        "Use exactly one of the listed intents and only its declared slots.\n"
        "If the request is not a supported automation, answer"
        ' {"intent": "unknown", "slots": {}}.\n'
        f"Request: {text}"
    )


def _extract_object(raw: str) -> dict[str, Any] | None:
    text = _FENCE.sub("", str(raw or "")).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT.search(text)
        if match is None:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _clean_slots(intent: str, raw_slots: Any) -> dict[str, Any] | None:
    if raw_slots is None:
        return {}
    if not isinstance(raw_slots, dict):
        return None
    allowed = set(spec_for(intent).required_slots) | set(spec_for(intent).optional_slots)
    cleaned: dict[str, Any] = {}
    for key, value in raw_slots.items():
        slot = str(key)
        if slot not in allowed:
            return None
        if isinstance(value, str):
            if value.strip():
                cleaned[slot] = value.strip()
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            if value:
                cleaned[slot] = [item.strip() for item in value if item.strip()]
        else:
            return None
    return cleaned


def parse_suggestion(raw: str) -> LLMSuggestion | None:
    """Validate a raw model answer against the frozen schema (None: reject)."""
    parsed = _extract_object(raw)
    if parsed is None:
        return None
    intent = parsed.get("intent", parsed.get("action", ""))
    if not isinstance(intent, str):
        return None
    intent = intent.strip()
    if intent not in INTENTS or intent in ("unknown", "help"):
        return None
    slots = _clean_slots(intent, parsed.get("slots", {}))
    if slots is None:
        return None
    return LLMSuggestion(intent=intent, slots=slots, raw=str(raw or ""))


class LLMPlanningAdapter:
    """One typed suggestion from a model client (single attempt, item [60])."""

    def __init__(self, client: ModelClient, *, timeout_s: float = 5.0) -> None:
        self.client = client
        self.timeout_s = float(timeout_s)

    @property
    def provider(self) -> str:
        return str(getattr(self.client, "provider", "unknown"))

    @property
    def model(self) -> str:
        return str(getattr(self.client, "model", "unknown"))

    def suggest(self, text: str) -> LLMSuggestion | None:
        """Ask the model once; return a schema-valid suggestion or None."""
        try:
            raw = self.client.complete(prompt_for(text), timeout_s=self.timeout_s)
        except ModelError:
            return None
        return parse_suggestion(raw)
