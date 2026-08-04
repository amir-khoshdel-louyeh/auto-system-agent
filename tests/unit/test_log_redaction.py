"""Phase 9 item [85]: structured logs never carry credentials or PII.

Three layers are checked: sensitive keys are dropped structurally, the frozen
scrub rules mask anything that looks like a secret inside free text (a bearer
token pasted into a message, a CLI `--password x`, an email), and the JSON
formatter plus the access middleware emit exactly the fields they promise —
method, path, status, duration, address digest — with no header or query
string anywhere in the line.
"""

import asyncio
import io
import json
import logging
import unittest

from app.logging import (
    ACCESS_LOGGER,
    REDACTED,
    AccessLogMiddleware,
    JsonFormatter,
    client_digest,
    configure_logging,
    is_secret_key,
    redact_mapping,
    redact_text,
    redact_value,
)

BEARER = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.c2lnbmF0dXJl"  # secret-scan: allow - dummy JWT fixture


class KeyRedactionTests(unittest.TestCase):
    def test_secret_key_names_are_recognised(self):
        for key in ("password", "Authorization", "API-KEY", "secret_key", "Set-Cookie"):
            with self.subTest(key=key):
                self.assertTrue(is_secret_key(key))
        for key in ("username", "workflow_id", "duration_ms", "status"):
            with self.subTest(key=key):
                self.assertFalse(is_secret_key(key))

    def test_nested_secret_keys_are_replaced_whatever_they_hold(self):
        payload = {
            "user": "admin",
            "fields": {"password": "hunter2", "api_key": ["sk-live-1", "sk-live-2"]},
            "headers": {"authorization": BEARER},
            "limits": {"rate_limit_per_minute": 1200},
        }
        clean = redact_value(payload)
        self.assertEqual(clean["user"], "admin")
        self.assertEqual(clean["fields"]["password"], REDACTED)
        self.assertEqual(clean["fields"]["api_key"], REDACTED)
        self.assertEqual(clean["headers"]["authorization"], REDACTED)
        self.assertEqual(clean["limits"]["rate_limit_per_minute"], 1200)
        self.assertNotIn("hunter2", json.dumps(clean))

    def test_a_sensitive_container_key_hides_its_whole_subtree(self):
        clean = redact_value({"credentials": {"password": "hunter2"}})
        self.assertEqual(clean, {"credentials": REDACTED})

    def test_text_values_are_scrubbed_even_under_a_harmless_key(self):
        clean = redact_value({"detail": "login failed for alice@example.com"})
        self.assertNotIn("alice@example.com", clean["detail"])
        self.assertIn("[email]", clean["detail"])

    def test_non_string_scalars_survive(self):
        clean = redact_value({"status": 429, "duration_ms": 1.5, "ok": False, "none": None})
        self.assertEqual(clean["status"], 429)
        self.assertEqual(clean["duration_ms"], 1.5)
        self.assertFalse(clean["ok"])
        self.assertIsNone(clean["none"])

    def test_mapping_keys_are_stringified(self):
        clean = redact_mapping({1: "one", "token": "abc"})
        self.assertEqual(clean["1"], "one")
        self.assertEqual(clean["token"], REDACTED)


class TextRedactionTests(unittest.TestCase):
    def test_bearer_token_in_a_message_is_masked(self):
        masked = redact_text(f"calling api with {BEARER}")
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", masked)
        self.assertIn("[token]", masked)

    def test_cli_password_is_masked(self):
        masked = redact_text("running install --password hunter2 --yes")
        self.assertNotIn("hunter2", masked)
        self.assertIn("[secret]", masked)

    def test_email_and_home_path_are_masked(self):
        masked = redact_text("plan by alice@example.com from /home/alice/workflows")
        self.assertNotIn("alice@example.com", masked)
        self.assertNotIn("/home/alice", masked)

    def test_plain_text_is_untouched(self):
        self.assertEqual(redact_text("node read SUCCEEDED in 12ms"), "node read SUCCEEDED in 12ms")


class FormatterTests(unittest.TestCase):
    def record(self, message: str, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_json_line_has_the_expected_fields_and_is_redacted(self):
        line = JsonFormatter().format(
            self.record(
                f"decision for {BEARER}",
                http={"path": "/approvals", "status": 200},
                fields={"password": "hunter2"},
                user="alice@example.com",
            )
        )
        payload = json.loads(line)
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "app.test")
        self.assertIn("ts", payload)
        self.assertEqual(payload["http"]["status"], 200)
        self.assertEqual(payload["fields"], {"password": REDACTED})
        self.assertNotIn("hunter2", line)
        self.assertNotIn(BEARER, line)
        self.assertNotIn("alice@example.com", line)

    def test_exception_text_is_redacted(self):
        try:
            raise ValueError(f"cannot use {BEARER}")
        except ValueError:
            import sys

            record = self.record("failed")
            record.exc_info = sys.exc_info()
        payload = json.loads(JsonFormatter().format(record))
        self.assertIn("ValueError", payload["exception"])
        self.assertNotIn(BEARER, payload["exception"])

    def test_configure_logging_writes_json_to_the_given_stream(self):
        stream = io.StringIO()
        root = logging.getLogger()
        saved = list(root.handlers)
        saved_level = root.level
        try:
            configure_logging("INFO", stream=stream)
            logging.getLogger("app.probe").info("hello", extra={"password": "hunter2"})
        finally:
            for handler in list(root.handlers):
                root.removeHandler(handler)
            for handler in saved:
                root.addHandler(handler)
            root.setLevel(saved_level)
        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["message"], "hello")
        self.assertEqual(payload["password"], REDACTED)


def scope(path="/executions", **overrides):
    base = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"token=super-secret-query",
        "headers": [(b"authorization", BEARER.encode())],
        "client": ("192.0.2.7", 4444),
    }
    base.update(overrides)
    return base


class AccessLogTests(unittest.TestCase):
    def run_middleware(self, status=200, **scope_overrides):
        async def app(inner_scope, receive, send):
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_content": False}

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        middleware = AccessLogMiddleware(app)
        with self.assertLogs(ACCESS_LOGGER, level="INFO") as captured:
            asyncio.run(middleware(scope(**scope_overrides), receive, send))
        return captured.records[0]

    def test_one_line_per_request_with_operational_fields(self):
        record = self.run_middleware()
        payload = record.http  # type: ignore[attr-defined]
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["path"], "/executions")
        self.assertEqual(payload["status"], 200)
        self.assertIsInstance(payload["duration_ms"], float)

    def test_headers_and_query_string_never_reach_the_log(self):
        record = self.run_middleware()
        rendered = f"{record.getMessage()} {record.__dict__}"
        self.assertNotIn("super-secret-query", rendered)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", rendered)

    def test_peer_address_is_logged_as_a_digest(self):
        record = self.run_middleware()
        digest = record.http["client"]  # type: ignore[attr-defined]
        self.assertEqual(digest, client_digest("192.0.2.7"))
        self.assertEqual(len(digest), 12)
        self.assertNotIn("192.0.2.7", str(record.__dict__))

    def test_a_rejected_request_is_still_logged(self):
        record = self.run_middleware(status=429)
        self.assertEqual(record.http["status"], 429)  # type: ignore[attr-defined]


class AppWiringTests(unittest.TestCase):
    """The factory wires the middleware, and production installs JSON logs."""

    def build_client(self, **overrides):
        import tempfile
        from pathlib import Path

        from fastapi.testclient import TestClient

        from app.main import create_app
        from app.settings import Settings

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        values = {
            "admin_user": "admin",
            "admin_password": "log-admin-pw-123",
            "secret_key": "logging-secret-0123456789abcd",
            "sqlite_path": str(Path(tmp.name) / "logs.db"),
        }
        values.update(overrides)
        return TestClient(create_app(Settings(**values)))

    def test_every_request_produces_one_access_line(self):
        client = self.build_client()
        with self.assertLogs(ACCESS_LOGGER, level="INFO") as captured:
            client.get("/health")
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].http["path"], "/health")  # type: ignore[attr-defined]

    def test_login_line_carries_no_credentials(self):
        client = self.build_client()
        with self.assertLogs(ACCESS_LOGGER, level="INFO") as captured:
            client.post("/auth/login", json={"username": "admin", "password": "log-admin-pw-123"})
        rendered = " ".join(
            f"{record.getMessage()} {record.__dict__}" for record in captured.records
        )
        self.assertNotIn("log-admin-pw-123", rendered)

    def test_production_settings_install_the_json_handler(self):
        root = logging.getLogger()
        saved, level = list(root.handlers), root.level
        try:
            client = self.build_client(
                environment="production",
                secret_key="prod-secret-0123456789abcdefghij",
                admin_password="prod-admin-pw-123",
                postgres_password="prod-postgres-pw-123",
            )
            handlers = logging.getLogger().handlers
            self.assertTrue(any(isinstance(h.formatter, JsonFormatter) for h in handlers))
            self.assertEqual(client.get("/health").status_code, 200)
        finally:
            for handler in list(root.handlers):
                root.removeHandler(handler)
            for handler in saved:
                root.addHandler(handler)
            root.setLevel(level)


if __name__ == "__main__":
    unittest.main()
