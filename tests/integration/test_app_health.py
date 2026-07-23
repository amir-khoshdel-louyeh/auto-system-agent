"""Phase 0 (local-only, not committed): GET /health."""

import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


class AppHealthTests(unittest.TestCase):
    def test_health_ok(self):
        client = TestClient(create_app(Settings(app_version="0.1.0")))
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["version"], "0.1.0")


if __name__ == "__main__":
    unittest.main()
