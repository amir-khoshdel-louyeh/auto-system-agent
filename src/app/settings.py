"""Phase 0 settings: stdlib-only, env-based.

Mirrors the shape of source_MIT/FastAPI-boilerplate backend settings
(DatabaseSettings/AuthSettings/AppSettings) without pydantic-settings,
crudauth or taskiq. Every value has a dev default so `pytest` and
`docker compose up` both work; production overrides via environment.

Phase 1 item [10]: settings are now *validated* - unknown environments,
out-of-range values and, in production, development default secrets are
rejected at startup (`SettingsError`) instead of shipping silently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .roles import parse_role

#: Values that must never appear in a production deployment.
DEFAULT_SECRET_KEY = "insecure-secret-key-change-this"
DEFAULT_PASSWORDS = frozenset({"admin", "change-me", "password", "changeme", "postgres"})
ENVIRONMENTS = frozenset({"development", "staging", "production", "pytest"})


class SettingsError(RuntimeError):
    """Configuration is invalid; fail closed at startup, never at request time."""


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Flat settings container (explicit ctor args keep tests env-free)."""

    app_name: str = "Auto System Agent API"
    app_version: str = "0.1.0"
    environment: str = "development"  # development | staging | production | pytest
    secret_key: str = DEFAULT_SECRET_KEY
    admin_user: str = "admin"
    admin_password: str = "admin"  # dev default only; override APP_ADMIN_PASSWORD
    admin_role: str = "OWNER"  # role minted into env-admin JWTs (item [12])
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_server: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "postgres"
    database_url_override: str = ""
    redis_url: str = "redis://localhost:6379/0"
    token_ttl_minutes: int = 60
    sqlite_path: str = "data/app.db"  # auth/user database (items [11]-[15])
    # Phase 9 item [84]: ingress guards (see app.limits).
    max_request_bytes: int = 1_048_576
    rate_limit_per_minute: int = 1200
    login_rate_limit_per_minute: int = 10

    def __post_init__(self) -> None:
        self.validate()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        """Fail fast on impossible or unsafe configuration.

        Development keeps working defaults (proposal: clean `pytest` and
        `docker compose up` from a clone); production must be configured
        explicitly and may not reuse development defaults.
        """
        if self.environment not in ENVIRONMENTS:
            raise SettingsError(
                f"ENVIRONMENT must be one of {sorted(ENVIRONMENTS)}, got {self.environment!r}"
            )
        if not self.secret_key.strip():
            raise SettingsError("SECRET_KEY must not be empty")
        if self.token_ttl_minutes < 1:
            raise SettingsError("TOKEN_TTL_MINUTES must be >= 1")
        if not 1 <= self.postgres_port <= 65535:
            raise SettingsError(f"POSTGRES_PORT out of range: {self.postgres_port}")
        if not self.redis_url.startswith(("redis://", "rediss://", "unix://")):
            raise SettingsError(f"REDIS_URL must be a redis scheme, got {self.redis_url!r}")
        if not self.sqlite_path.strip():
            raise SettingsError("SQLITE_PATH must not be empty")
        if self.max_request_bytes < 1024:
            raise SettingsError("MAX_REQUEST_BYTES must be >= 1024")
        if self.rate_limit_per_minute < 1:
            raise SettingsError("RATE_LIMIT_PER_MINUTE must be >= 1")
        if self.login_rate_limit_per_minute < 1:
            raise SettingsError("LOGIN_RATE_LIMIT_PER_MINUTE must be >= 1")
        try:
            parse_role(self.admin_role)
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc
        if self.is_production:
            if self.secret_key == DEFAULT_SECRET_KEY or len(self.secret_key) < 32:
                raise SettingsError(
                    "SECRET_KEY must be replaced with a >=32 char random value in production"
                )
            if self.admin_password.lower() in DEFAULT_PASSWORDS:
                raise SettingsError("APP_ADMIN_PASSWORD still uses a development default")
            if self.postgres_password.lower() in DEFAULT_PASSWORDS:
                raise SettingsError("POSTGRES_PASSWORD still uses a development default")

    @property
    def database_url(self) -> str:
        """Placeholder URL (no driver yet); async SQLAlchemy arrives next phase."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_server}:{self.postgres_port}/{self.postgres_db}"
        )


def get_settings() -> Settings:
    """Build Settings from the environment (called per-request factory by default)."""
    return Settings(
        app_name=_get("APP_NAME", "Auto System Agent API"),
        app_version=_get("APP_VERSION", "0.1.0"),
        environment=_get("ENVIRONMENT", "development"),
        secret_key=_get("SECRET_KEY", "insecure-secret-key-change-this"),
        admin_user=_get("APP_ADMIN_USER", "admin"),
        admin_password=_get("APP_ADMIN_PASSWORD", "admin"),
        postgres_user=_get("POSTGRES_USER", "postgres"),
        postgres_password=_get("POSTGRES_PASSWORD", "postgres"),
        postgres_server=_get("POSTGRES_SERVER", "localhost"),
        postgres_port=_get_int("POSTGRES_PORT", 5432),
        postgres_db=_get("POSTGRES_DB", "postgres"),
        database_url_override=_get("DATABASE_URL", ""),
        redis_url=_get("REDIS_URL", "redis://localhost:6379/0"),
        token_ttl_minutes=_get_int("TOKEN_TTL_MINUTES", 60),
        admin_role=_get("APP_ADMIN_ROLE", "OWNER"),
        sqlite_path=_get("SQLITE_PATH", "data/app.db"),
        max_request_bytes=_get_int("MAX_REQUEST_BYTES", 1_048_576),
        rate_limit_per_minute=_get_int("RATE_LIMIT_PER_MINUTE", 1200),
        login_rate_limit_per_minute=_get_int("LOGIN_RATE_LIMIT_PER_MINUTE", 10),
    )
