"""Phase 6 item [60]: typed LLM adapter behind an explicit interface.

The adapter only ever returns schema-valid suggestions: an unknown intent, an
undeclared slot, a wrong value type or a non-JSON answer is rejected, and the
model is reached through an injected client so nothing touches the network.
"""

import json
import unittest

from orchestration.llm_adapter import (
    LLMPlanningAdapter,
    LLMSuggestion,
    ModelClient,
    ModelError,
    ModelTimeout,
    parse_suggestion,
    prompt_for,
)


class FakeClient:
    """A scripted ModelClient: records prompts, returns or raises in order."""

    provider = "fake"
    model = "fake-1"

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.timeouts: list[float] = []

    def complete(self, prompt: str, *, timeout_s: float) -> str:
        self.prompts.append(prompt)
        self.timeouts.append(timeout_s)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def payload(intent: str, slots: dict | None = None) -> str:
    return json.dumps({"intent": intent, "slots": slots or {}})


class PromptTests(unittest.TestCase):
    def test_prompt_lists_the_frozen_vocabulary(self):
        prompt = prompt_for("install firefox")
        self.assertIn("install_app", prompt)
        self.assertIn("create_folder", prompt)
        self.assertIn("Request: install firefox", prompt)
        self.assertIn("strict JSON", prompt)


class ParseSuggestionTests(unittest.TestCase):
    def test_valid_suggestion(self):
        suggestion = parse_suggestion(payload("install_app", {"app": "vlc"}))
        self.assertEqual(suggestion.intent, "install_app")
        self.assertEqual(suggestion.slots, {"app": "vlc"})

    def test_code_fences_and_prose_are_tolerated(self):
        raw = "Sure!\n```json\n" + payload("create_folder", {"folder_names": ["logs"]}) + "\n```"
        self.assertEqual(parse_suggestion(raw).intent, "create_folder")

    def test_unknown_intent_is_rejected(self):
        self.assertIsNone(parse_suggestion(payload("teleport")))

    def test_help_and_unknown_intents_are_rejected(self):
        self.assertIsNone(parse_suggestion(payload("unknown")))
        self.assertIsNone(parse_suggestion(payload("help")))

    def test_undeclared_slot_is_rejected(self):
        self.assertIsNone(parse_suggestion(payload("install_app", {"vibe": "yes"})))

    def test_wrong_value_type_is_rejected(self):
        self.assertIsNone(parse_suggestion(payload("install_app", {"app": 7})))
        self.assertIsNone(parse_suggestion(payload("create_folder", {"folder_names": [1, 2]})))

    def test_non_json_and_empty_answers_are_rejected(self):
        for raw in ("", "   ", "I cannot help with that", "{not json", "[1, 2]"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_suggestion(raw))

    def test_blank_slot_values_are_dropped(self):
        suggestion = parse_suggestion(payload("install_app", {"app": "   "}))
        self.assertEqual(suggestion.slots, {})

    def test_missing_slots_key_is_allowed(self):
        self.assertEqual(parse_suggestion(json.dumps({"intent": "help"})), None)
        self.assertEqual(parse_suggestion(json.dumps({"intent": "create_folder"})).slots, {})


class AdapterTests(unittest.TestCase):
    def test_protocol_conformance_of_the_fake(self):
        self.assertIsInstance(FakeClient(), ModelClient)

    def test_adapter_returns_a_validated_suggestion(self):
        client = FakeClient(payload("system_info", {"metric": "disk"}))
        adapter = LLMPlanningAdapter(client, timeout_s=2.5)
        suggestion = adapter.suggest("how is the disk doing")
        self.assertIsInstance(suggestion, LLMSuggestion)
        self.assertEqual(suggestion.intent, "system_info")
        self.assertEqual(adapter.provider, "fake")
        self.assertEqual(adapter.model, "fake-1")
        self.assertEqual(client.timeouts, [2.5])
        self.assertIn("how is the disk doing", client.prompts[0])

    def test_invalid_answer_is_not_a_suggestion(self):
        adapter = LLMPlanningAdapter(FakeClient("no idea"), max_attempts=1)
        self.assertIsNone(adapter.suggest("do something"))

    def test_model_errors_are_swallowed_into_none(self):
        for error in (ModelTimeout("slow"), ModelError("boom")):
            with self.subTest(error=type(error).__name__):
                adapter = LLMPlanningAdapter(FakeClient(error), max_attempts=1)
                self.assertIsNone(adapter.suggest("install vlc"))

    def test_unknown_client_identity_is_reported_safely(self):
        class Anonymous:
            def complete(self, prompt: str, *, timeout_s: float) -> str:
                return payload("help")

        adapter = LLMPlanningAdapter(Anonymous())
        self.assertEqual(adapter.provider, "unknown")
        self.assertEqual(adapter.model, "unknown")


class RetryAndFallbackTests(unittest.TestCase):
    """Item [61]: timeouts, retries, provider failure and fallbacks."""

    def test_timeout_is_retried_on_the_same_provider(self):
        client = FakeClient(ModelTimeout("slow"), payload("install_app", {"app": "vlc"}))
        adapter = LLMPlanningAdapter(client, max_attempts=3)
        suggestion = adapter.suggest("install vlc")
        self.assertEqual(suggestion.intent, "install_app")
        self.assertEqual(
            [attempt.outcome for attempt in adapter.attempts],
            [
                "timeout",
                "ok",
            ],
        )
        self.assertEqual(len(client.prompts), 2)

    def test_retries_stop_at_max_attempts(self):
        client = FakeClient(*[ModelTimeout("slow")] * 5)
        adapter = LLMPlanningAdapter(client, max_attempts=2)
        self.assertIsNone(adapter.suggest("install vlc"))
        self.assertEqual([attempt.outcome for attempt in adapter.attempts], ["timeout", "timeout"])
        self.assertEqual(len(client.prompts), 2)

    def test_invalid_response_is_retried_then_gives_up(self):
        client = FakeClient("not json", "still not json")
        adapter = LLMPlanningAdapter(client, max_attempts=2)
        self.assertIsNone(adapter.suggest("install vlc"))
        self.assertEqual(
            [attempt.outcome for attempt in adapter.attempts],
            ["invalid_response", "invalid_response"],
        )

    def test_provider_error_moves_on_without_retrying(self):
        primary = FakeClient(ModelError("connection refused"))
        backup = FakeClient(payload("create_folder", {"folder_names": ["logs"]}))
        backup.provider = "backup"
        adapter = LLMPlanningAdapter(primary, fallbacks=(backup,), max_attempts=3)
        suggestion = adapter.suggest("create a folder called logs")
        self.assertEqual(suggestion.intent, "create_folder")
        self.assertEqual(len(primary.prompts), 1, "no retry after a provider error")
        outcomes = [attempt.outcome for attempt in adapter.attempts]
        self.assertEqual(outcomes, ["provider_error", "ok"])
        self.assertFalse(adapter.attempts[0].fallback_used)
        self.assertTrue(adapter.attempts[1].fallback_used)
        self.assertEqual(adapter.attempts[1].provider, "backup")

    def test_timeout_falls_back_to_the_next_provider(self):
        primary = FakeClient(ModelTimeout("slow"))
        backup = FakeClient(payload("system_info", {"metric": "disk"}))
        adapter = LLMPlanningAdapter(primary, fallbacks=(backup,), max_attempts=1)
        suggestion = adapter.suggest("how much disk space is free")
        self.assertEqual(suggestion.slots, {"metric": "disk"})
        self.assertEqual([attempt.outcome for attempt in adapter.attempts], ["timeout", "ok"])

    def test_every_provider_failing_returns_none(self):
        adapter = LLMPlanningAdapter(
            FakeClient(ModelError("down"), ModelError("down")),
            fallbacks=(FakeClient(ModelTimeout("slow")),),
            max_attempts=1,
        )
        self.assertIsNone(adapter.suggest("install vlc"))
        self.assertTrue(adapter.attempts)

    def test_backoff_sleeps_between_attempts(self):
        slept: list[float] = []
        client = FakeClient(ModelTimeout("slow"), payload("help"))
        adapter = LLMPlanningAdapter(client, max_attempts=2, backoff_s=0.5, sleeper=slept.append)
        adapter.suggest("install vlc")
        self.assertEqual(slept, [0.5])

    def test_timeout_is_passed_to_every_call(self):
        client = FakeClient(ModelTimeout("slow"), payload("help"))
        adapter = LLMPlanningAdapter(client, timeout_s=1.25, max_attempts=2)
        adapter.suggest("install vlc")
        self.assertEqual(client.timeouts, [1.25, 1.25])

    def test_attempt_log_is_serialisable(self):
        client = FakeClient(payload("install_app", {"app": "vlc"}))
        adapter = LLMPlanningAdapter(client)
        adapter.suggest("install vlc")
        body = adapter.attempts[0].as_dict()
        self.assertEqual(
            set(body),
            {"provider", "model", "attempt", "outcome", "latency_ms", "fallback_used"},
        )
        self.assertGreaterEqual(body["latency_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
