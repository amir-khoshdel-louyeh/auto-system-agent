"""Phase 7 items [70], [71]: memory API and the erasure guarantee.

The API is the user-facing surface of the same store: consent is the
caller's own record, storing needs consent (409 otherwise), stored plans are
scrubbed and quarantined, search only returns reusable plans, and erasure
removes the item while the consent history stays.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import create_token
from app.db import init_db
from app.main import create_app
from app.settings import Settings
from memory.consent import ConsentStore
from memory.store import MemoryStore

SECRET = "memory-secret-0123456789abcdef"
PLAN = {
    "intent": "create_workspace",
    "steps": [{"id": "create-demo-app", "type": "shell", "run": "mkdir -p demo-app"}],
}


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            admin_user="admin",
            admin_password="memory-admin-pw",
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "auth.db"),
        )
        self.client = TestClient(create_app(self.settings))
        self.token = self.client.post(
            "/auth/login", json={"username": "admin", "password": "memory-admin-pw"}
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _store_plan(self, **overrides):
        body = {"plan": PLAN, "summary": "create demo-app workspace", "intent": "create_workspace"}
        body.update(overrides)
        return self.client.post("/memory/items", json=body, headers=self.headers)

    # -- authorization ---------------------------------------------------
    def test_endpoints_require_authentication(self):
        for method, url in (
            ("get", "/memory/items"),
            ("get", "/memory/search?q=workspace"),
            ("get", "/memory/consent"),
            ("delete", "/memory/items/abc"),
            ("post", "/memory/consent"),
        ):
            with self.subTest(url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 401)

    def test_viewer_may_read_but_not_store(self):
        viewer = create_token("viewer", SECRET, 10, role="VIEWER")
        headers = {"Authorization": f"Bearer {viewer}"}
        self.assertEqual(self.client.get("/memory/items", headers=headers).status_code, 200)
        self.assertEqual(
            self.client.post("/memory/items", json={"plan": PLAN}, headers=headers).status_code,
            403,
        )

    # -- consent ---------------------------------------------------------
    def test_consent_lifecycle(self):
        self.assertFalse(self.client.get("/memory/consent", headers=self.headers).json()["active"])
        granted = self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        self.assertEqual(granted.status_code, 200)
        self.assertTrue(granted.json()["active"])
        revoked = self.client.post("/memory/consent", json={"granted": False}, headers=self.headers)
        self.assertFalse(revoked.json()["active"])
        self.assertIn("revoked_at", revoked.json())

    def test_revoking_without_a_record_is_404(self):
        response = self.client.post(
            "/memory/consent", json={"granted": False}, headers=self.headers
        )
        self.assertEqual(response.status_code, 404)

    # -- storing ---------------------------------------------------------
    def test_store_without_consent_is_conflict(self):
        response = self._store_plan()
        self.assertEqual(response.status_code, 409)
        self.assertIn("consent", response.json()["detail"])

    def test_stored_plan_starts_quarantined_and_scrubbed(self):
        self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        response = self._store_plan(
            plan={"intent": "run_command", "steps": [{"run": "mail alice@example.com"}]}
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["state"], "QUARANTINED")
        self.assertFalse(body["reusable"])
        self.assertGreater(body["pii_findings"], 0)

        stored = MemoryStore(init_db(self.settings.sqlite_path)).get(body["id"])
        self.assertNotIn("alice@example.com", stored.canonical_plan)

    def test_unknown_risk_is_rejected(self):
        self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        self.assertEqual(self._store_plan(risk="CATASTROPHIC").status_code, 422)

    # -- search ----------------------------------------------------------
    def test_search_excludes_quarantined_plans(self):
        self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        stored = self._store_plan().json()
        empty = self.client.get(
            "/memory/search", params={"q": "create demo-app workspace"}, headers=self.headers
        ).json()
        self.assertEqual(empty["hits"], [])

        db = init_db(self.settings.sqlite_path)
        store = MemoryStore(db)
        store.record_episode(stored["id"], success=True)
        store.record_episode(stored["id"], success=True, rating=5)

        hits = self.client.get(
            "/memory/search", params={"q": "create demo-app workspace"}, headers=self.headers
        ).json()
        self.assertEqual(hits["hits"][0]["id"], stored["id"])
        self.assertEqual(hits["hits"][0]["state"], "PUBLIC")

    def test_list_filters_by_state_and_rejects_unknown_states(self):
        self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        self._store_plan()
        listed = self.client.get("/memory/items", headers=self.headers).json()
        self.assertEqual(listed["total"], 1)
        quarantined = self.client.get(
            "/memory/items", params={"state": "QUARANTINED"}, headers=self.headers
        ).json()
        self.assertEqual(quarantined["total"], 1)
        self.assertEqual(
            self.client.get(
                "/memory/items", params={"state": "MYSTERY"}, headers=self.headers
            ).status_code,
            422,
        )

    # -- erasure ---------------------------------------------------------
    def test_erasure_removes_the_item_and_keeps_consent_history(self):
        self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        item_id = self._store_plan().json()["id"]

        erased = self.client.delete(f"/memory/items/{item_id}", headers=self.headers)
        self.assertEqual(erased.status_code, 200)
        self.assertTrue(erased.json()["erased"])

        self.assertEqual(self.client.get("/memory/items", headers=self.headers).json()["total"], 0)
        db = init_db(self.settings.sqlite_path)
        self.assertEqual(MemoryStore(db).count(), 0)
        consents = ConsentStore(db).list("admin")
        self.assertEqual(len(consents), 1)
        self.assertTrue(consents[0].active)

    def test_erasing_twice_reports_not_found(self):
        self.client.post("/memory/consent", json={"granted": True}, headers=self.headers)
        item_id = self._store_plan().json()["id"]
        self.client.delete(f"/memory/items/{item_id}", headers=self.headers)
        second = self.client.delete(f"/memory/items/{item_id}", headers=self.headers)
        self.assertEqual(second.status_code, 404)


if __name__ == "__main__":
    unittest.main()
