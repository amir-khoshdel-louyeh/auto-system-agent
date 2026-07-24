"""Phase 0-1 auth: env-admin JSON login minting a short-lived HS256 JWT.

The JWT codec and password hashing live in ``app.security`` (Phase 1 item
[11], FR-01); this module keeps the HTTP surface (``POST /auth/login``)
plus the token helpers reused by tests and by the authorization
dependencies added in item [13]. ``Settings`` (item [10]) supplies the
secret and TTL.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .security import TokenError, decode_token, encode_token
from .settings import Settings, get_settings


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


def create_token(username: str, secret: str, ttl_minutes: int, *, role: str = "VIEWER") -> str:
    """Mint an HS256 JWT carrying ``sub`` + ``role`` (item [12]).

    ``role`` defaults to the least-privileged VIEWER so a token minted
    without an explicit role can never authorize a privileged action.
    """
    return encode_token({"sub": username, "role": role}, secret, ttl_seconds=ttl_minutes * 60)


def verify_token(token: str, secret: str) -> str | None:
    """Return the subject when alg/signature/expiry all verify, else ``None``."""
    try:
        claims = decode_token(token, secret)
    except TokenError:
        return None
    return claims["sub"]


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, settings: Settings = Depends(get_settings)) -> TokenOut:
    """Verify env-admin credentials (constant-time) and mint a bearer JWT."""
    if body.username != settings.admin_user or not _constant_time_eq(
        body.password, settings.admin_password
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenOut(
        access_token=create_token(
            body.username,
            settings.secret_key,
            settings.token_ttl_minutes,
            role=settings.admin_role,
        )
    )


def _constant_time_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
