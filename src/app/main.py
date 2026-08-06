"""Phase 0 FastAPI factory: app assembly + GET /health.

Mirrors source_MIT/FastAPI-boilerplate `interfaces/main.py` (+200-line
app_factory) reduced to the MVP surface: title/version from Settings,
one system router. Auth router is wired in the auth commit.
"""

from __future__ import annotations

from fastapi import FastAPI

from .admin import router as admin_router
from .approvals_api import router as approvals_router
from .audit_api import metrics_router
from .audit_api import router as audit_router
from .auth import router as auth_router
from .limits import RequestLimits
from .logging import AccessLogMiddleware, configure_logging
from .memory_api import router as memory_router
from .policy_api import router as policy_router
from .run_service import RunService
from .settings import Settings, get_settings
from .workflows_api import router as workflows_router
from .workspaces import teams_router, workspaces_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Assemble the Phase 0 API (explicit Settings keeps tests env-free)."""
    settings = settings or get_settings()
    application = FastAPI(title=settings.app_name, version=settings.app_version)
    application.state.settings = settings
    # Item [104]: one run service per app. Tests may replace it with one that
    # pins a deterministic shell runner; production keeps the real sandbox.
    application.state.run_service = RunService(settings.sqlite_path)
    # Route dependencies (e.g. login) must see the same Settings object
    # the factory was built with, not a fresh env read.
    application.dependency_overrides[get_settings] = lambda: settings

    # Phase 9 items [84]/[85]: rate limits inside, redacted access log outside,
    # so even a rejected 429 is logged as one structured line.
    application.add_middleware(RequestLimits, settings=settings)
    application.add_middleware(AccessLogMiddleware)
    if settings.environment in ("staging", "production"):
        configure_logging(settings.log_level)

    @application.get("/health", tags=["System"])
    def health_check() -> dict[str, str]:
        """Health probe for compose/docker and load balancers."""
        return {"status": "healthy", "version": settings.app_version}

    application.include_router(auth_router)
    application.include_router(admin_router)
    application.include_router(teams_router)
    application.include_router(workspaces_router)
    application.include_router(workflows_router)
    application.include_router(audit_router)
    application.include_router(metrics_router)
    application.include_router(memory_router)
    application.include_router(policy_router)
    application.include_router(approvals_router)
    return application


app = create_app()


if __name__ == "__main__":  # pragma: no cover - local dev entrypoint
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
