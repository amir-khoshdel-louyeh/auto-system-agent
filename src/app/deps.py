"""Phase 1 item [13]: authorization dependencies for protected routes (FR-02).

Two FastAPI dependencies enforce RBAC at the API layer:

- :func:`get_current_user` - verifies the bearer JWT (401 without/against a
  valid token) and resolves the signed ``role`` claim;
- :func:`require_permission` - wraps it and denies with 403 unless the
  role holds the required :class:`Permission`.

Routes declare *what* they need; knowing an object's identifier never
grants access (FR-02). Service layers re-check the same matrix, so the
dependency is defence in depth, not the only gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from .roles import Permission, Role, has_permission, parse_role, permissions_for
from .security import TokenError, decode_token
from .settings import Settings, get_settings

BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True)
class CurrentUser:
    """Authenticated identity as proven by a verified JWT."""

    username: str
    role: Role

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def permissions(self) -> list[str]:
        return sorted(p.value for p in permissions_for(self.role))


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=BEARER_CHALLENGE,
    )


def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Resolve the bearer JWT into a user; 401 on anything invalid."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Missing bearer token")
    try:
        claims = decode_token(token, settings.secret_key)
        role = parse_role(claims.get("role"))
    except (TokenError, ValueError) as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc
    return CurrentUser(username=claims["sub"], role=role)


def require_permission(permission: Permission) -> Callable[..., CurrentUser]:
    """Dependency factory: authenticated **and** holding ``permission``."""

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.can(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(f"role {user.role.value} lacks permission {permission.value}"),
            )
        return user

    return dependency
