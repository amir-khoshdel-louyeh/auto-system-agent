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
        adapter = LLMPlanningAdapter(FakeClient("no idea"))
        self.assertIsNone(adapter.suggest("do something"))

    def test_model_errors_are_swallowed_into_none(self):
        for error in (ModelTimeout("slow"), ModelError("boom")):
            with self.subTest(error=type(error).__name__):
                adapter = LLMPlanningAdapter(FakeClient(error))
                self.assertIsNone(adapter.suggest("install vlc"))

    def test_unknown_client_identity_is_reported_safely(self):
        class Anonymous:
            def complete(self, prompt: str, *, timeout_s: float) -> str:
                return payload("help")

        adapter = LLMPlanningAdapter(Anonymous())
        self.assertEqual(adapter.provider, "unknown")
        self.assertEqual(adapter.model, "unknown")


if __name__ == "__main__":
    unittest.main()
