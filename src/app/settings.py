"""Phase 0 settings: stdlib-only, env-based.

Mirrors the shape of source_MIT/FastAPI-boilerplate backend settings
(DatabaseSettings/AuthSettings/AppSettings) without pydantic-settings,
crudauth or taskiq. Every value has a dev default so `pytest` and
`docker compose up` both work; production overrides via environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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
    secret_key: str = "insecure-secret-key-change-this"
    admin_user: str = "admin"
    admin_password: str = "admin"  # dev default only; override APP_ADMIN_PASSWORD
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_server: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "postgres"
    database_url_override: str = ""
    redis_url: str = "redis://localhost:6379/0"
    token_ttl_minutes: int = 60

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
    )
