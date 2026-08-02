"""Phase 6 item [62]: provider metadata recorded, secrets never stored.

The offline planner stays authoritative: the model is consulted only when the
offline answer needs clarification, its suggestion still passes the shared
trust rules and the same validator/policy pipeline, and every consultation is
recorded as secret-free provider metadata.
"""

import json
import unittest

from orchestration.llm_adapter import (
    LLMPlanningAdapter,
    ModelError,
    ModelTimeout,
)
from orchestration.offline_planner import PlannerConfig
from orchestration.plan_compiler import compile_plan
from orchestration.planner_service import LLM_CONFIDENCE, PlannerService

API_KEY = "sk-super-secret-0123456789"


class FakeClient:
    provider = "fake"
    model = "fake-1"

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        # A credential the adapter must never record.
        self.api_key = API_KEY
        self.url = f"https://user:{API_KEY}@example.test/v1/chat"

    def complete(self, prompt: str, *, timeout_s: float) -> str:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def payload(intent: str, slots: dict | None = None) -> str:
    return json.dumps({"intent": intent, "slots": slots or {}})


def service_with(client, **kwargs) -> PlannerService:
    adapter = LLMPlanningAdapter(client, **kwargs)
    return PlannerService(adapter=adapter)


class OfflineFirstTests(unittest.TestCase):
    def test_offline_plan_is_used_without_an_adapter(self):
        plan = PlannerService().plan("install firefox")
        self.assertEqual(plan.planner, "offline")
        self.assertTrue(plan.executable)
        self.assertEqual(plan.metadata, {})

    def test_confident_offline_plan_does_not_consult_the_model(self):
        client = FakeClient(payload("install_app", {"app": "wrong"}))
        plan = service_with(client).plan("install firefox")
        self.assertEqual(plan.planner, "offline")
        self.assertEqual(
            client.answers, [payload("install_app", {"app": "wrong"})], "model untouched"
        )

    def test_help_never_consults_the_model(self):
        client = FakeClient(payload("install_app", {"app": "vlc"}))
        plan = service_with(client).plan("what can you do")
        self.assertEqual(plan.intent, "help")
        self.assertEqual(len(client.answers), 1)

    def test_disabled_provider_keeps_the_deterministic_answer(self):
        # Item [61]/[63]: no adapter or a dead provider must not change the
        # planner's answer.
        offline = PlannerService().plan("do the thing")
        with_provider = service_with(FakeClient(ModelError("down"))).plan("do the thing")
        self.assertEqual(with_provider.clarification, offline.clarification)
        self.assertFalse(with_provider.executable)


class LLMFallbackTests(unittest.TestCase):
    def test_low_confidence_request_gets_a_validated_llm_plan(self):
        client = FakeClient(payload("create_folder", {"folder_names": ["logs"]}))
        plan = service_with(client).plan("please make the thing")
        self.assertEqual(plan.planner, "llm")
        self.assertEqual(plan.intent, "create_folder")
        self.assertEqual(plan.confidence, LLM_CONFIDENCE)
        self.assertTrue(plan.executable)

    def test_llm_plan_still_passes_the_shared_pipeline(self):
        client = FakeClient(payload("create_workspace", {"workspace_name": "demo"}))
        plan = service_with(client).plan("set something up for the project")
        workflow = compile_plan(plan, name="llm-planned")
        self.assertEqual(workflow.name, "llm-planned")
        self.assertEqual(len(workflow.nodes), len(plan.steps))

    def test_llm_suggestion_still_needs_its_slots(self):
        client = FakeClient(payload("install_app", {}))
        plan = service_with(client).plan("please handle that for me")
        self.assertTrue(plan.needs_clarification)
        self.assertFalse(plan.executable)
        self.assertEqual(plan.clarification, "Which application should I install?")

    def test_invalid_llm_answer_falls_back_to_offline_clarification(self):
        client = FakeClient("I refuse", "I still refuse")
        plan = service_with(client, max_attempts=2).plan("do the thing")
        self.assertTrue(plan.needs_clarification)
        self.assertIn("could not map", plan.clarification)
        self.assertEqual(plan.metadata["llm"]["outcome"], "invalid_response")

    def test_raising_the_threshold_also_gates_llm_plans(self):
        client = FakeClient(payload("create_folder", {"folder_names": ["logs"]}))
        service = PlannerService(
            config=PlannerConfig(confidence_threshold=0.95),
            adapter=LLMPlanningAdapter(client),
        )
        plan = service.plan("please make the thing")
        self.assertTrue(plan.needs_clarification)
        self.assertFalse(plan.executable)


class MetadataTests(unittest.TestCase):
    def test_metadata_records_provider_identity_and_timing(self):
        client = FakeClient(payload("system_info", {"metric": "disk"}))
        plan = service_with(client, timeout_s=1.5).plan("please handle that for me")
        metadata = plan.metadata["llm"]
        self.assertEqual(metadata["provider"], "fake")
        self.assertEqual(metadata["model"], "fake-1")
        self.assertEqual(metadata["attempts"], 1)
        self.assertEqual(metadata["outcome"], "ok")
        self.assertGreaterEqual(metadata["latency_ms"], 0.0)
        self.assertFalse(metadata["fallback_used"])

    def test_metadata_records_fallback_and_failure(self):
        primary = FakeClient(ModelTimeout("slow"))
        backup = FakeClient(payload("create_folder", {"folder_names": ["logs"]}))
        backup.provider, backup.model = "backup", "backup-1"
        service = service_with(primary, fallbacks=(backup,), max_attempts=1)
        plan = service.plan("please make the thing")
        metadata = plan.metadata["llm"]
        self.assertEqual(metadata["attempts"], 2)
        self.assertEqual(metadata["outcome"], "ok")
        self.assertEqual(metadata["provider"], "backup")
        self.assertTrue(metadata["fallback_used"])

    def test_metadata_never_contains_secrets(self):
        client = FakeClient(payload("system_info", {"metric": "disk"}))
        plan = service_with(client).plan("please handle that for me")
        self.assertIn("llm", plan.metadata, "the consultation must be recorded")
        serialised = json.dumps(plan.metadata)
        self.assertNotIn(API_KEY, serialised)
        self.assertNotIn("api_key", serialised)
        self.assertNotIn("bearer", serialised.lower())
        self.assertNotIn("example.test", serialised)
        for forbidden in ("api_key", "authorization", "token", "url", "secret"):
            self.assertNotIn(forbidden, serialised)

    def test_failed_consultation_still_records_metadata(self):
        client = FakeClient(ModelError("connection refused"))
        plan = service_with(client).plan("do the thing")
        metadata = plan.metadata["llm"]
        self.assertEqual(metadata["outcome"], "provider_error")
        self.assertEqual(metadata["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
