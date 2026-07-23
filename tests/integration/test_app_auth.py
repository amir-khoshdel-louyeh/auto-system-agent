"""Phase 0 (local-only, not committed): POST /auth/login."""

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import verify_token
from app.main import create_app
from app.settings import Settings

def _client() -> TestClient:
    settings = Settings(admin_user="admin", admin_password="s3cret", secret_key="test-key")
    return TestClient(create_app(settings))

class AppAuthTests(unittest.TestCase):
    def test_login_ok_mints_verifiable_token(self):
        resp = _client().post("/auth/login", json={"username": "admin", "password": "s3cret"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(verify_token(body["access_token"], "test-key"), "admin")

    def test_login_wrong_password_401(self):
        resp = _client().post("/auth/login", json={"username": "admin", "password": "nope"})
        self.assertEqual(resp.status_code, 401)

    def test_login_unknown_user_401(self):
        resp = _client().post("/auth/login", json={"username": "root", "password": "s3cret"})
        self.assertEqual(resp.status_code, 401)

    def test_tampered_token_rejected(self):
        token = _client().post(
            "/auth/login", json={"username": "admin", "password": "s3cret"}
        ).json()["access_token"]
        self.assertIsNone(verify_token(token + "x", "test-key"))
        self.assertIsNone(verify_token(token, "other-key"))

if __name__ == "__main__":
    unittest.main()
