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
import time
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


#: Attempt outcomes recorded for observability (item [62]).
OUTCOME_OK = "ok"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_PROVIDER_ERROR = "provider_error"
OUTCOME_INVALID = "invalid_response"


@dataclass(frozen=True)
class LLMAttempt:
    """One call to one provider, with its measured latency and outcome."""

    provider: str
    model: str
    attempt: int
    outcome: str
    latency_ms: float
    fallback_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "attempt": self.attempt,
            "outcome": self.outcome,
            "latency_ms": self.latency_ms,
            "fallback_used": self.fallback_used,
        }


#: Keys that must never appear in recorded metadata (item [62]).
SECRET_KEY_HINTS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "credential",
        "password",
        "secret",
        "token",
        "url",
    }
)


@dataclass(frozen=True)
class LLMMetadata:
    """What the planner records about one model consultation (item [62]).

    Only provider identity, attempts, outcome and timing: no endpoint, no
    credential and no raw prompt or answer ever reaches the audit trail.
    """

    provider: str
    model: str
    attempts: int
    outcome: str
    latency_ms: float
    fallback_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "outcome": self.outcome,
            "latency_ms": self.latency_ms,
            "fallback_used": self.fallback_used,
        }


def sanitize_metadata(value: Any) -> Any:
    """Recursively drop secret-shaped keys before metadata is recorded."""
    if isinstance(value, dict):
        return {
            str(key): sanitize_metadata(item)
            for key, item in value.items()
            if str(key).lower() not in SECRET_KEY_HINTS
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_metadata(item) for item in value]
    return value


class LLMPlanningAdapter:
    """Typed suggestions with timeout, retry, provider fallback (items [60], [61]).

    Failure policy (item [61]):

    - a *timeout* or an *invalid response* retries the same provider up to
      ``max_attempts`` with ``backoff_s`` between tries;
    - a *provider error* moves straight to the next configured provider;
    - when every provider is exhausted the adapter returns ``None`` so the
      caller keeps its deterministic offline answer — the model can never
      turn a failure into a guessed command.
    """

    def __init__(
        self,
        client: ModelClient,
        *,
        timeout_s: float = 5.0,
        max_attempts: int = 2,
        backoff_s: float = 0.0,
        fallbacks: tuple[ModelClient, ...] = (),
        sleeper=None,
    ) -> None:
        self.client = client
        self.fallbacks = tuple(fallbacks)
        self.timeout_s = float(timeout_s)
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_s = max(0.0, float(backoff_s))
        self._sleep = sleeper
        self.attempts: list[LLMAttempt] = []

    @property
    def clients(self) -> tuple[ModelClient, ...]:
        """Primary provider first, then the configured fallbacks."""
        return (self.client, *self.fallbacks)

    @property
    def provider(self) -> str:
        return str(getattr(self.client, "provider", "unknown"))

    @property
    def model(self) -> str:
        return str(getattr(self.client, "model", "unknown"))

    def _wait(self) -> None:
        if self.backoff_s <= 0:
            return
        if self._sleep is not None:
            self._sleep(self.backoff_s)
        else:  # pragma: no cover - only in production, never in tests
            import time

            time.sleep(self.backoff_s)

    def suggest(self, text: str) -> LLMSuggestion | None:
        """Best schema-valid suggestion across providers, or None."""
        prompt = prompt_for(text)
        for index, client in enumerate(self.clients):
            provider = str(getattr(client, "provider", "unknown"))
            model = str(getattr(client, "model", "unknown"))
            for attempt in range(1, self.max_attempts + 1):
                if attempt > 1:
                    self._wait()
                started = time.monotonic()
                try:
                    raw = client.complete(prompt, timeout_s=self.timeout_s)
                except ModelTimeout:
                    self._record(provider, model, attempt, OUTCOME_TIMEOUT, started, index)
                    continue
                except ModelError:
                    self._record(provider, model, attempt, OUTCOME_PROVIDER_ERROR, started, index)
                    break
                latency = (time.monotonic() - started) * 1000.0
                suggestion = parse_suggestion(raw)
                if suggestion is None:
                    self.attempts.append(
                        LLMAttempt(
                            provider=provider,
                            model=model,
                            attempt=attempt,
                            outcome=OUTCOME_INVALID,
                            latency_ms=round(latency, 2),
                            fallback_used=index > 0,
                        )
                    )
                    continue
                self.attempts.append(
                    LLMAttempt(
                        provider=provider,
                        model=model,
                        attempt=attempt,
                        outcome=OUTCOME_OK,
                        latency_ms=round(latency, 2),
                        fallback_used=index > 0,
                    )
                )
                return suggestion
        return None

    def metadata(self) -> LLMMetadata:
        """Aggregated, secret-free metadata for the last consultation (item [62])."""
        last = self.attempts[-1] if self.attempts else None
        payload = {
            "provider": last.provider if last else self.provider,
            "model": last.model if last else self.model,
            "attempts": len(self.attempts),
            "outcome": last.outcome if last else OUTCOME_OK,
            "latency_ms": round(sum(attempt.latency_ms for attempt in self.attempts), 2),
            "fallback_used": any(attempt.fallback_used for attempt in self.attempts),
        }
        clean = sanitize_metadata(payload)
        return LLMMetadata(**clean)

    def _record(
        self,
        provider: str,
        model: str,
        attempt: int,
        outcome: str,
        started: float,
        index: int,
    ) -> None:
        self.attempts.append(
            LLMAttempt(
                provider=provider,
                model=model,
                attempt=attempt,
                outcome=outcome,
                latency_ms=round((time.monotonic() - started) * 1000.0, 2),
                fallback_used=index > 0,
            )
        )
