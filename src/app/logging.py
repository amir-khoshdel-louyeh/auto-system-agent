"""Phase 9 item [85]: structured logs that cannot leak secrets (FR-16, NFR).

One JSON object per line (`configure_logging`), and one access line per
request (`AccessLogMiddleware`). Two layers of redaction, in this order:

1. **structural** — a field whose key names a credential (`password`,
   `authorization`, `token`, `api_key`, `cookie`, `secret_key`, …) is replaced
   with ``"[redacted]"`` no matter what it contains;
2. **textual** — the frozen Phase 7 rule table (`memory.scrub`) is applied to
   every remaining string, so a token pasted into a message, a CLI
   ``--password x``, an email or a home path is masked before it is written.

The access log deliberately records only method, route path, status,
duration and a *digest* of the peer address: no headers, no query string (it
can carry tokens), no body, and no raw address (a stable 12-hex digest still
lets one client's requests be correlated). Exceptions are rendered through
the same redaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from memory.scrub import scrub

REDACTED = "[redacted]"

#: Field names that are never logged, whatever they hold.
SECRET_KEYS = frozenset(
    {
        "access_token",
        "apikey",
        "api_key",
        "auth",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "jwt",
        "passwd",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "session",
        "set_cookie",
        "token",
    }
)

ACCESS_LOGGER = "app.access"


def is_secret_key(key: Any) -> bool:
    """True for key names that carry credentials (case/`-`/`_` insensitive)."""
    if not isinstance(key, str):
        return False
    return key.strip().lower().replace("-", "_") in SECRET_KEYS


def redact_value(value: Any) -> Any:
    """Structurally redact sensitive keys, then scrub every remaining string."""
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return scrub(value).text
    return value


def redact_text(text: str) -> str:
    """Scrub one message through the frozen PII/secret rules."""
    return scrub("" if text is None else str(text)).text


def redact_mapping(mapping: dict[Any, Any]) -> dict[str, Any]:
    """Redact one mapping: sensitive keys are masked, values are scrubbed."""
    return {
        str(key): REDACTED if is_secret_key(key) else redact_value(value)
        for key, value in mapping.items()
    }


def client_digest(host: str, *, length: int = 12) -> str:
    """Stable short digest of a peer address (correlate, never disclose)."""
    return hashlib.sha256(str(host).encode("utf-8")).hexdigest()[:length]


#: Attributes set by `logging` itself; anything else on the record is an extra.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """One redacted JSON object per record, extras merged as fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        payload.update(redact_mapping(extras))
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, stream: Any = None) -> logging.Logger:
    """Install the JSON handler on the root logger (idempotent per process)."""
    root = logging.getLogger()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    return root


class AccessLogMiddleware:
    """Log one structured line per HTTP request; never log its content."""

    def __init__(self, app: ASGIApp, *, level: int = logging.INFO) -> None:
        self.app = app
        self.level = level

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 0

        async def spy(message: Message) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, spy)
        finally:
            client = scope.get("client")
            host = str(client[0]) if isinstance(client, (tuple, list)) and client else "unknown"
            logging.getLogger(ACCESS_LOGGER).log(
                self.level,
                "%s %s -> %d",
                scope.get("method", "GET"),
                scope.get("path", ""),
                status,
                extra={
                    "http": {
                        "method": scope.get("method", "GET"),
                        "path": scope.get("path", ""),
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 3),
                        "client": client_digest(host),
                    }
                },
            )
