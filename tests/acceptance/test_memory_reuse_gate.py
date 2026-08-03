"""Phase 7 gate: a safe plan is reusable, a failed/poisoned one never is.

The gate is exercised through the API a user actually calls: consent, store,
sandbox episodes, search and metrics. A plan only becomes a reuse hit after
it has been proven in the sandbox; the same plan that fails afterwards drops
back to quarantine and immediately stops being returned, and a plan carrying
personal data is stored scrubbed (or refused), never reused verbatim.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import create_app
from app.settings import Settings
from memory.store import MemoryStore

SECRET = "gate-secret-0123456789abcdef"
SAFE_PLAN = {
    "intent": "create_workspace",
    "steps": [{"id": "create-demo-app", "type": "shell", "run": "mkdir -p demo-app"}],
}
POISONED_PLAN = {
    "intent": "run_command",
    "steps": [{"id": "notify", "run": "printf 'done' | mail alice@example.com"}],
}


class MemoryReuseGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            admin_user="admin",
            admin_password="gate-admin-pw",
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "gate.db"),
        )
        self.client = TestClient(create_app(self.settings))
        token = self.client.post(
            "/auth/login", json={"username": "admin", "password": "gate-admin-pw"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}
        granted = self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        self.assertEqual(granted.status_code, 200)

    def _store(self, plan, summary):
        response = self.client.post(
            "/memory/items",
            json={"plan": plan, "summary": summary, "intent": plan["intent"]},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _episode(self, item_id, *, success, rating=None):
        response = self.client.post(
            f"/memory/items/{item_id}/episodes",
            json={"success": success, "rating": rating},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _search(self, query):
        return self.client.get("/memory/search", params={"q": query}, headers=self.headers).json()

    def test_safe_plan_is_reused_only_after_sandbox_proof(self):
        item = self._store(SAFE_PLAN, "create demo-app workspace")
        self.assertEqual(item["state"], "QUARANTINED")

        # Unproven: quarantine keeps it out of retrieval, so a plan is generated.
        self.assertEqual(self._search("create demo-app workspace")["hits"], [])

        self.assertEqual(self._episode(item["id"], success=True)["state"], "SANDBOX")
        promoted = self._episode(item["id"], success=True, rating=5)
        self.assertEqual(promoted["state"], "PUBLIC")
        self.assertTrue(promoted["reusable"])

        hits = self._search("create demo-app workspace")
        self.assertEqual(hits["hits"][0]["id"], item["id"])
        self.assertEqual(hits["hits"][0]["state"], "PUBLIC")

        metrics = self.client.get("/memory/metrics", headers=self.headers).json()
        self.assertEqual(metrics["requests"], 2)
        self.assertEqual(metrics["reuse_hits"], 1)
        self.assertEqual(metrics["planner_calls_avoided"], 1)
        self.assertEqual(metrics["planner_generations"], 1)

    def test_failed_plan_is_quarantined_and_not_reused(self):
        item = self._store(SAFE_PLAN, "create demo-app workspace")
        self._episode(item["id"], success=True)
        self._episode(item["id"], success=True, rating=5)
        self.assertEqual(self._search("create demo-app workspace")["hits"][0]["id"], item["id"])

        failed = self._episode(item["id"], success=False)
        self.assertEqual(failed["state"], "QUARANTINED")
        self.assertFalse(failed["reusable"])
        self.assertEqual(self._search("create demo-app workspace")["hits"], [])

        hits = self.client.get("/memory/items", headers=self.headers).json()
        self.assertEqual(hits["items"][0]["success_count"], 2)
        self.assertEqual(hits["items"][0]["reuse_count"], 3)

    def test_poisoned_plan_is_scrubbed_before_it_can_be_reused(self):
        item = self._store(POISONED_PLAN, "notify alice@example.com")
        self.assertGreater(item["pii_findings"], 0)

        stored = MemoryStore(init_db(self.settings.sqlite_path)).get(item["id"])
        self.assertNotIn("alice@example.com", stored.canonical_plan)
        self.assertIn("[email]", stored.canonical_plan)

        self._episode(item["id"], success=True)
        self._episode(item["id"], success=True, rating=5)
        hits = self._search("notify alice@example.com")
        self.assertEqual(hits["hits"][0]["id"], item["id"])
        self.assertNotIn("alice@example.com", str(hits["hits"]))

        # The retrieval metric keeps the query digest, never the raw text.
        from memory.usage import list_retrievals

        recorded = list_retrievals(init_db(self.settings.sqlite_path))
        self.assertEqual(len(recorded), 1)
        self.assertNotIn("alice@example.com", str(recorded))

    def test_unknown_item_episode_is_404(self):
        response = self.client.post(
            "/memory/items/missing/episodes", json={"success": True}, headers=self.headers
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
