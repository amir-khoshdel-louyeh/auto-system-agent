"""Phase 9 item [84]: ingress guards on the real app.

Login brute force is throttled after a small number of *failed* attempts and
a successful login refunds its token, so the suite's own repeated sign-ins
keep working. General routes get their own bucket, health is exempt, and an
oversized body is refused with 413 before pydantic ever sees it.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

SECRET = "rate-limit-secret-0123456789abcd"
PASSWORD = "rate-limit-admin-pw"


class RateLimitCase(unittest.TestCase):
    def build(self, **overrides) -> TestClient:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        values = {
            "admin_user": "admin",
            "admin_password": PASSWORD,
            "secret_key": SECRET,
            "sqlite_path": str(Path(self._tmp.name) / "limits.db"),
        }
        values.update(overrides)
        self.settings = Settings(**values)
        return TestClient(create_app(self.settings))

    def login(self, client: TestClient, password: str = PASSWORD):
        return client.post("/auth/login", json={"username": "admin", "password": password})


class LoginThrottleTests(RateLimitCase):
    def test_failed_attempts_then_429_with_retry_after(self):
        client = self.build(login_rate_limit_per_minute=3)
        for _ in range(3):
            self.assertEqual(self.login(client, "wrong-password").status_code, 401)
        blocked = self.login(client, "wrong-password")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("rate limit", blocked.json()["detail"])
        self.assertIn("Retry-After", blocked.headers)
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)

    def test_successful_login_is_refunded(self):
        client = self.build(login_rate_limit_per_minute=1)
        for _ in range(4):
            self.assertEqual(self.login(client).status_code, 200)

    def test_a_success_after_failures_still_works(self):
        client = self.build(login_rate_limit_per_minute=3)
        self.assertEqual(self.login(client, "wrong").status_code, 401)
        self.assertEqual(self.login(client).status_code, 200)


class GeneralLimitTests(RateLimitCase):
    def test_general_routes_share_a_bucket(self):
        client = self.build(rate_limit_per_minute=2)
        token = self.login(client).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(client.get("/executions", headers=headers).status_code, 200)
        self.assertEqual(client.get("/executions", headers=headers).status_code, 200)
        self.assertEqual(client.get("/executions", headers=headers).status_code, 429)

    def test_health_is_exempt(self):
        client = self.build(rate_limit_per_minute=1)
        for _ in range(5):
            self.assertEqual(client.get("/health").status_code, 200)

    def test_limits_are_per_client_not_global(self):
        from app.limits import RateLimiter

        limiter = RateLimiter(per_minute=1)
        self.assertTrue(limiter.check("10.0.0.1")[0])
        self.assertFalse(limiter.check("10.0.0.1")[0])
        self.assertTrue(limiter.check("10.0.0.2")[0])


class BodyCapTests(RateLimitCase):
    def test_oversized_body_is_413(self):
        client = self.build(max_request_bytes=1024)
        token = self.login(client).json()["access_token"]
        response = client.post(
            "/workflows/validate",
            json={"dsl": "x" * 4096},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("exceeds", response.json()["detail"])

    def test_body_within_the_cap_is_handled_normally(self):
        client = self.build(max_request_bytes=10_000)
        token = self.login(client).json()["access_token"]
        response = client.post(
            "/workflows/validate",
            json={"dsl": "workflow: demo\nnodes: []\n"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertIn(response.status_code, (200, 422))


if __name__ == "__main__":
    unittest.main()
