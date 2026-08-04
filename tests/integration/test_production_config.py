"""Phase 9 item [83]: production ships no development default secret.

Two halves of the same claim. Settings already refuse dev defaults when
``ENVIRONMENT=production``; the deployment file must therefore *require*
every credential (compose's ``${VAR:?message}`` form) instead of falling back
to one, must not publish the database or broker, and must never mention the
development secret. Both are asserted here, so a "convenient" default cannot
sneak back in with a compose edit.

Parsing is textual on purpose: the project is stdlib-only and does not depend
on a YAML library.
"""

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app.settings import DEFAULT_SECRET_KEY, Settings, SettingsError, get_settings

ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = ROOT / "compose.prod.yml"
DEV_COMPOSE = ROOT / "compose.yml"

#: Secrets a production deployment must supply itself.
REQUIRED_SECRETS = (
    "SECRET_KEY",
    "APP_ADMIN_PASSWORD",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "APP_ADMIN_USER",
)

PRODUCTION_ENV = {
    "SECRET_KEY": "prod-secret-0123456789abcdefghijklmnop",
    "APP_ADMIN_USER": "admin",
    "APP_ADMIN_PASSWORD": "prod-admin-password-123",
    "POSTGRES_USER": "agent",
    "POSTGRES_PASSWORD": "prod-postgres-password-123",
    "POSTGRES_DB": "agent",
    "REDIS_PASSWORD": "prod-redis-password-123",
}


def service_blocks(text: str) -> dict[str, str]:
    """Split a compose file into ``{service: block}`` (stdlib-only parse)."""
    blocks: dict[str, list[str]] = {}
    inside_services = False
    current = ""
    for line in text.splitlines():
        if line.startswith("services:"):
            inside_services = True
            continue
        if line.startswith(("volumes:", "networks:")):
            inside_services = False
            current = ""
            continue
        if not inside_services:
            continue
        if re.match(r"^  [a-z_]+:\s*$", line):
            current = line.strip().rstrip(":")
            blocks[current] = []
        elif current:
            blocks[current].append(line)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def non_comment_lines(text: str) -> str:
    """Only the effective lines of a compose file (comments may discuss policy)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


class ProductionComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PROD_COMPOSE.read_text(encoding="utf-8")
        cls.dev = DEV_COMPOSE.read_text(encoding="utf-8")
        cls.services = service_blocks(cls.text)

    def test_the_file_exists_and_targets_production(self):
        self.assertTrue(PROD_COMPOSE.is_file())
        self.assertIn("ENVIRONMENT: production", self.text)
        self.assertIn("name: auto-system-agent-prod", self.text)

    def test_every_secret_is_required_not_defaulted(self):
        for name in REQUIRED_SECRETS:
            with self.subTest(variable=name):
                self.assertIn(f"${{{name}:?", self.text)

    def test_no_credential_has_a_fallback_value(self):
        offenders = [
            line.strip()
            for line in non_comment_lines(self.text).splitlines()
            if re.search(r"\$\{(?:%s):-" % "|".join(REQUIRED_SECRETS), line)
        ]
        self.assertEqual(offenders, [], "production must not fall back to a dev value")

    def test_the_development_secret_never_appears_as_a_value(self):
        self.assertNotIn(DEFAULT_SECRET_KEY, non_comment_lines(self.text))

    def test_the_three_services_are_declared(self):
        self.assertEqual(sorted(self.services), ["api", "db", "redis"])

    def publishes(self, service: str) -> bool:
        """True when the service declares a real ``ports:`` mapping."""
        effective = non_comment_lines(self.services[service])
        return bool(re.search(r"^\s*ports:\s*$", effective, re.MULTILINE))

    def test_database_and_broker_are_not_published(self):
        for service in ("db", "redis"):
            with self.subTest(service=service):
                self.assertFalse(self.publishes(service), f"{service} must stay internal")
        self.assertTrue(self.publishes("api"))

    def test_api_is_published_on_loopback_only(self):
        self.assertIn('"127.0.0.1:8000:8000"', self.text)

    def test_redis_requires_authentication_and_the_url_carries_it(self):
        self.assertIn("--requirepass", self.services["redis"])
        self.assertIn("redis://:${REDIS_PASSWORD:?", self.services["api"])

    def test_the_development_stack_still_works_from_a_clone(self):
        # The dev file keeps its defaults on purpose; that is the difference
        # between the two files and it must stay explicit.
        self.assertIn("${SECRET_KEY:-insecure-secret-key-change-this}", self.dev)
        self.assertIn("ENVIRONMENT: ${ENVIRONMENT:-development}", self.dev)


class ProductionSettingsTests(unittest.TestCase):
    def test_production_accepts_fully_supplied_secrets(self):
        settings = Settings(
            environment="production",
            secret_key=PRODUCTION_ENV["SECRET_KEY"],
            admin_user=PRODUCTION_ENV["APP_ADMIN_USER"],
            admin_password=PRODUCTION_ENV["APP_ADMIN_PASSWORD"],
            postgres_user=PRODUCTION_ENV["POSTGRES_USER"],
            postgres_password=PRODUCTION_ENV["POSTGRES_PASSWORD"],
            postgres_db=PRODUCTION_ENV["POSTGRES_DB"],
        )
        self.assertTrue(settings.is_production)

    def test_production_rejects_each_missing_piece(self):
        cases = {
            "SECRET_KEY": {"secret_key": DEFAULT_SECRET_KEY},
            "SHORT_SECRET_KEY": {"secret_key": "too-short"},
            "APP_ADMIN_PASSWORD": {"admin_password": "change-me"},
            "POSTGRES_PASSWORD": {"postgres_password": "postgres"},
        }
        base = {
            "secret_key": PRODUCTION_ENV["SECRET_KEY"],
            "admin_password": PRODUCTION_ENV["APP_ADMIN_PASSWORD"],
            "postgres_password": PRODUCTION_ENV["POSTGRES_PASSWORD"],
        }
        for name, override in cases.items():
            with self.subTest(missing=name):
                with self.assertRaises(SettingsError):
                    Settings(environment="production", **{**base, **override})

    def test_environment_driven_settings_fail_closed(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=True):
            with self.assertRaises(SettingsError) as caught:
                get_settings()
        self.assertIn("SECRET_KEY", str(caught.exception))

    def test_environment_driven_settings_accept_the_production_values(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production", **PRODUCTION_ENV}, clear=True):
            settings = get_settings()
        self.assertTrue(settings.is_production)
        self.assertEqual(settings.log_level, "INFO")


if __name__ == "__main__":
    unittest.main()
