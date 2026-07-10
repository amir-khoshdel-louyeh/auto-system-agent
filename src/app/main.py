"""Phase 0 FastAPI factory: app assembly + GET /health.

Mirrors source_MIT/FastAPI-boilerplate `interfaces/main.py` (+200-line
app_factory) reduced to the MVP surface: title/version from Settings,
one system router. Auth router is wired in the auth commit.
"""

from __future__ import annotations

from fastapi import FastAPI

from .settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Assemble the Phase 0 API (explicit Settings keeps tests env-free)."""
    settings = settings or get_settings()
    application = FastAPI(title=settings.app_name, version=settings.app_version)
    application.state.settings = settings

    @application.get("/health", tags=["System"])
    def health_check() -> dict[str, str]:
        """Health probe for compose/docker and load balancers."""
        return {"status": "healthy", "version": settings.app_version}

    return application


app = create_app()


if __name__ == "__main__":  # pragma: no cover - local dev entrypoint
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
