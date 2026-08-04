"""Phase 9 item [84]: request-size and per-client rate limits (FR-02, NFR).

Two cheap, deterministic guards sit in front of every route:

- **body cap** — a request that declares a ``Content-Length`` above
  ``max_request_bytes`` (default 1 MiB) is answered with 413 before any
  handler reads it. A body-bearing method without a declared length is
  answered with 411, so a caller cannot stream an unbounded document past
  the cap. This is deliberately strict: the API is JSON-in/JSON-out and a
  chunked upload from a proxy is not a supported request shape.
- **token bucket** — one bucket per client address and bucket name. General
  routes use ``rate_limit_per_minute``; ``/auth/login`` uses the much smaller
  ``login_rate_limit_per_minute`` because that route is the brute-force
  target. A *successful* login refunds its token, so legitimate repeated
  sign-ins are never throttled while failed guesses are. ``/health`` is never
  limited, so probes stay cheap.

Both answers are JSON ``{"detail": ...}`` with ``Retry-After`` on a 429, so
clients (including the Tkinter workbench) can surface a real message. The
buckets live in process memory: one API replica is limited exactly; behind
several replicas an edge limiter is still required (see
``docs/THREAT_MODEL.md`` §5).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .settings import Settings

#: Methods that carry a body; they must declare how large it is.
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

#: Paths that bypass both guards.
UNLIMITED_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})

LOGIN_PATH = "/auth/login"

#: Hard ceiling on tracked buckets, so a spoofed-address flood cannot grow
#: the limiter without bound. Idle buckets are dropped when crossed.
MAX_TRACKED_KEYS = 10_000


@dataclass
class TokenBucket:
    """Continuous-refill token bucket (capacity = burst size)."""

    capacity: float
    per_second: float
    tokens: float = 0.0
    updated: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.updated = 0.0

    def take(self, now: float) -> tuple[bool, float]:
        """Consume one token; returns ``(allowed, retry_after_seconds)``."""
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.per_second)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0
        return False, (1.0 - self.tokens) / self.per_second

    def refund(self, amount: float = 1.0) -> None:
        """Give a token back (a successful login does not count)."""
        self.tokens = min(self.capacity, self.tokens + amount)

    @property
    def idle(self) -> bool:
        return self.tokens >= self.capacity


class RateLimiter:
    """Buckets keyed by ``"<client>|<name>"`` with bounded memory."""

    def __init__(
        self,
        *,
        per_minute: int,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_TRACKED_KEYS,
    ) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        self.per_minute = int(per_minute)
        self.burst = int(burst if burst is not None else max(1, per_minute))
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, client: str, name: str = "general") -> tuple[bool, float]:
        """Take one token for ``client``/``name``; ``(allowed, retry_after)``."""
        now = self.clock()
        key = f"{client}|{name}"
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self.max_keys:
                self._prune()
            bucket = TokenBucket(capacity=self.burst, per_second=self.per_minute / 60.0)
            self._buckets[key] = bucket
        return bucket.take(now)

    def refund(self, client: str, name: str = "general") -> None:
        bucket = self._buckets.get(f"{client}|{name}")
        if bucket is not None:
            bucket.refund()

    def _prune(self) -> None:
        """Drop untouched buckets first; never prune an active (drained) one."""
        for key in [key for key, bucket in self._buckets.items() if bucket.idle]:
            del self._buckets[key]
        if len(self._buckets) >= self.max_keys:
            # Still full of active keys: clear the oldest half rather than
            # letting memory grow without bound.
            for key in list(self._buckets)[: self.max_keys // 2]:
                del self._buckets[key]

    @property
    def tracked(self) -> int:
        return len(self._buckets)


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


def declared_length(scope: Scope) -> int | None:
    """``Content-Length`` as an int, or None when absent/unparseable."""
    raw = _header(scope, b"content-length").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def client_address(scope: Scope) -> str:
    """Best-effort caller identity: the socket peer (never a caller header)."""
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return "unknown"


def limit_name(path: str) -> str:
    return "login" if path == LOGIN_PATH else "general"


class RequestLimits:
    """ASGI middleware: body cap, token bucket, JSON 413/411/429 answers."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
    ) -> None:
        self.app = app
        self.max_request_bytes = int(settings.max_request_bytes)
        self.general = RateLimiter(per_minute=settings.rate_limit_per_minute)
        self.login = RateLimiter(per_minute=settings.login_rate_limit_per_minute)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if path in UNLIMITED_PATHS:
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        if method in BODY_METHODS:
            length = declared_length(scope)
            if length is None:
                await _error(
                    scope,
                    receive,
                    send,
                    411,
                    "request body must declare Content-Length",
                )
                return
            if length > self.max_request_bytes:
                await _error(
                    scope,
                    receive,
                    send,
                    413,
                    f"request body exceeds {self.max_request_bytes} bytes",
                )
                return

        client = client_address(scope)
        name = limit_name(path)
        limiter = self.login if name == "login" else self.general
        allowed, retry_after = limiter.check(client, name)
        if not allowed:
            await _error(
                scope,
                receive,
                send,
                429,
                "rate limit exceeded; retry later",
                retry_after=retry_after,
            )
            return

        status: dict[str, int] = {}

        async def spy(message: Message) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, spy)
        finally:
            if name == "login" and 200 <= status.get("code", 0) < 300:
                limiter.refund(client, name)


async def _error(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    detail: str,
    *,
    retry_after: float = 0.0,
) -> None:
    headers = {"Retry-After": str(max(1, int(retry_after + 0.999)))} if retry_after else {}
    response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
    await response(scope, receive, send)
