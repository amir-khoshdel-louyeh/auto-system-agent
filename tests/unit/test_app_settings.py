"""Phase 1 item [10]: settings and environment validation.

Covers the fail-fast contract: unknown environments, impossible ranges and
development-default secrets in production all raise SettingsError, while
the documented development defaults keep working (clean `pytest` / compose).
"""

import unittest

from app.settings import (
    DEFAULT_SECRET_KEY,
    Settings,
    SettingsError,
    get_settings,
)


class DevDefaultsTests(unittest.TestCase):
    def test_development_defaults_are_valid(self):
        settings = Settings()
        self.assertEqual(settings.environment, "development")
        self.assertFalse(settings.is_production)
        self.assertEqual(settings.secret_key, DEFAULT_SECRET_KEY)

    def test_get_settings_reads_environment(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"ENVIRONMENT": "pytest", "TOKEN_TTL_MINUTES": "5"}):
            settings = get_settings()
        self.assertEqual(settings.environment, "pytest")
        self.assertEqual(settings.token_ttl_minutes, 5)

    def test_get_settings_bad_int_falls_back(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"TOKEN_TTL_MINUTES": "not-a-number"}):
            self.assertEqual(get_settings().token_ttl_minutes, 60)


class ValidationTests(unittest.TestCase):
    def test_unknown_environment_rejected(self):
        with self.assertRaises(SettingsError):
            Settings(environment="staging2")

    def test_empty_secret_rejected(self):
        with self.assertRaises(SettingsError):
            Settings(secret_key="   ")

    def test_non_positive_token_ttl_rejected(self):
        with self.assertRaises(SettingsError):
            Settings(token_ttl_minutes=0)

    def test_postgres_port_range_enforced(self):
        with self.assertRaises(SettingsError):
            Settings(postgres_port=70000)
        with self.assertRaises(SettingsError):
            Settings(postgres_port=0)

    def test_redis_scheme_enforced(self):
        with self.assertRaises(SettingsError):
            Settings(redis_url="http://localhost:6379/0")


class ProductionHardeningTests(unittest.TestCase):
    def test_production_with_dev_secrets_rejected(self):
        with self.assertRaises(SettingsError):
            Settings(environment="production")  # default secret key

    def test_production_short_secret_rejected(self):
        with self.assertRaises(SettingsError):
            Settings(environment="production", secret_key="short")

    def test_production_default_admin_password_rejected(self):
        with self.assertRaises(SettingsError):
            Settings(
                environment="production",
                secret_key="x" * 40,
                admin_password="admin",
                postgres_password="s3cret-db-pass",
            )

    def test_production_default_postgres_password_rejected(self):
        with self.assertRaises(SettingsError):
            Settings(
                environment="production",
                secret_key="x" * 40,
                admin_password="s3cret-admin-pass",
                postgres_password="postgres",
            )

    def test_production_with_real_secrets_accepted(self):
        settings = Settings(
            environment="production",
            secret_key="y" * 48,
            admin_password="s3cret-admin-pass",
            postgres_password="s3cret-db-pass",
        )
        self.assertTrue(settings.is_production)


if __name__ == "__main__":
    unittest.main()
