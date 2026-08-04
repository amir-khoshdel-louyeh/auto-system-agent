"""Phase 9 item [84]: token buckets, body cap and the ASGI guard.

The guards are the only place a request is refused before routing, so they
are tested directly: bucket arithmetic (burst, refill, refund, bounded
memory), the scope helpers that decide *what* is limited, and the middleware
answers for a body that is too large (413) or undeclared (411).
"""

import asyncio
import unittest

from app.limits import (
    RateLimiter,
    RequestLimits,
    TokenBucket,
    client_address,
    declared_length,
    limit_name,
)
from app.settings import Settings


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TokenBucketTests(unittest.TestCase):
    def test_capacity_is_a_burst_then_denies_with_retry_after(self):
        bucket = TokenBucket(capacity=2, per_second=1.0)
        self.assertEqual(bucket.take(0.0), (True, 0.0))
        self.assertEqual(bucket.take(0.0), (True, 0.0))
        allowed, retry_after = bucket.take(0.0)
        self.assertFalse(allowed)
        self.assertAlmostEqual(retry_after, 1.0)

    def test_tokens_refill_over_time(self):
        bucket = TokenBucket(capacity=1, per_second=0.5)
        self.assertTrue(bucket.take(0.0)[0])
        self.assertFalse(bucket.take(0.9)[0])
        self.assertTrue(bucket.take(2.0)[0])

    def test_refund_never_exceeds_capacity(self):
        bucket = TokenBucket(capacity=1, per_second=1.0)
        bucket.refund(5)
        self.assertTrue(bucket.idle)
        self.assertTrue(bucket.take(0.0)[0])
        self.assertFalse(bucket.idle)


class RateLimiterTests(unittest.TestCase):
    def test_clients_and_buckets_are_isolated(self):
        clock = FakeClock()
        limiter = RateLimiter(per_minute=1, clock=clock)
        self.assertTrue(limiter.check("alice", "general")[0])
        self.assertFalse(limiter.check("alice", "general")[0])
        self.assertTrue(limiter.check("bob", "general")[0])
        self.assertTrue(limiter.check("alice", "login")[0])

    def test_refill_follows_the_configured_rate(self):
        clock = FakeClock()
        limiter = RateLimiter(per_minute=60, burst=1, clock=clock)  # one token/s
        self.assertTrue(limiter.check("alice")[0])
        self.assertFalse(limiter.check("alice")[0])
        clock.advance(1.0)
        self.assertTrue(limiter.check("alice")[0])

    def test_refund_restores_a_successful_login(self):
        clock = FakeClock()
        limiter = RateLimiter(per_minute=1, clock=clock)
        self.assertTrue(limiter.check("alice", "login")[0])
        self.assertFalse(limiter.check("alice", "login")[0])
        limiter.refund("alice", "login")
        self.assertTrue(limiter.check("alice", "login")[0])

    def test_invalid_rate_is_refused(self):
        with self.assertRaises(ValueError):
            RateLimiter(per_minute=0)

    def test_memory_is_bounded_under_key_flood(self):
        clock = FakeClock()
        limiter = RateLimiter(per_minute=1, clock=clock, max_keys=4)
        for index in range(50):
            limiter.check(f"client-{index}")
            clock.advance(0.01)
        self.assertLessEqual(limiter.tracked, 4)


def scope(**overrides):
    base = {
        "type": "http",
        "method": "GET",
        "path": "/executions",
        "headers": [],
        "client": ("192.0.2.7", 4444),
    }
    base.update(overrides)
    return base


class ScopeHelperTests(unittest.TestCase):
    def test_declared_length_parses_numbers_only(self):
        self.assertEqual(declared_length(scope(headers=[(b"content-length", b"12")])), 12)
        self.assertIsNone(declared_length(scope(headers=[])))
        self.assertIsNone(declared_length(scope(headers=[(b"content-length", b"abc")])))

    def test_client_address_ignores_forwarded_headers(self):
        self.assertEqual(client_address(scope()), "192.0.2.7")
        self.assertEqual(
            client_address(scope(client=None, headers=[(b"x-forwarded-for", b"1.2.3.4")])),
            "unknown",
        )

    def test_login_path_uses_its_own_bucket(self):
        self.assertEqual(limit_name("/auth/login"), "login")
        self.assertEqual(limit_name("/workflows/validate"), "general")


class MiddlewareTests(unittest.TestCase):
    """Drive the ASGI middleware without going through a route."""

    ROUTE = "/workflows/validate"

    def build(self, **settings_kwargs):
        calls: list[dict] = []

        async def app(inner_scope, receive, send):
            calls.append(inner_scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        settings = Settings(**settings_kwargs)
        return RequestLimits(app, settings=settings), calls

    def call(self, middleware, **scope_overrides):
        messages: list[dict] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(middleware(scope(**scope_overrides), receive, send))
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        headers = dict(next(m["headers"] for m in messages if m["type"] == "http.response.start"))
        return status, headers, messages

    def test_oversized_body_is_refused_before_the_route(self):
        middleware, calls = self.build(max_request_bytes=1024)
        status, _, _ = self.call(
            middleware,
            method="POST",
            path=self.ROUTE,
            headers=[(b"content-length", b"2048")],
        )
        self.assertEqual(status, 413)
        self.assertEqual(calls, [])

    def test_body_without_declared_length_is_refused(self):
        middleware, calls = self.build(max_request_bytes=1024)
        status, _, _ = self.call(middleware, method="POST", path=self.ROUTE, headers=[])
        self.assertEqual(status, 411)
        self.assertEqual(calls, [])

    def test_body_within_the_cap_reaches_the_route(self):
        middleware, calls = self.build(max_request_bytes=1024)
        status, _, _ = self.call(
            middleware,
            method="POST",
            path=self.ROUTE,
            headers=[(b"content-length", b"512")],
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(calls), 1)

    def test_general_flood_gets_429_and_retry_after(self):
        middleware, _ = self.build(rate_limit_per_minute=1)
        self.assertEqual(self.call(middleware)[0], 200)
        status, headers, messages = self.call(middleware)
        self.assertEqual(status, 429)
        self.assertIn(b"retry-after", headers)
        self.assertIn(b"rate limit", messages[-1]["body"])

    def test_health_is_never_limited(self):
        middleware, _ = self.build(rate_limit_per_minute=1)
        for _ in range(5):
            self.assertEqual(self.call(middleware, path="/health")[0], 200)

    def test_successful_login_refunds_its_token(self):
        middleware, _ = self.build(login_rate_limit_per_minute=1)
        for _ in range(3):
            self.assertEqual(
                self.call(
                    middleware,
                    method="POST",
                    path="/auth/login",
                    headers=[(b"content-length", b"2")],
                )[0],
                200,
            )

    def test_failed_login_consumes_the_login_bucket(self):
        async def refusing(inner_scope, receive, send):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        middleware = RequestLimits(refusing, settings=Settings(login_rate_limit_per_minute=1))
        self.assertEqual(self.call(middleware, path="/auth/login")[0], 401)
        self.assertEqual(self.call(middleware, path="/auth/login")[0], 429)


if __name__ == "__main__":
    unittest.main()
