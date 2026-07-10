"""Phase 0 auth: env-admin JSON login with stdlib HMAC token.

Boilerplate uses crudauth sessions + rate-limit + CSRF cookies; Phase 0 keeps
only the Done surface (POST /auth/login) with a bearer token signed by
SECRET_KEY. DB-backed users arrive with the migration-backed auth phase.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .settings import Settings, get_settings


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_token(username: str, secret: str, ttl_minutes: int) -> str:
    """Mint `payload.signature` where payload carries sub + exp (epoch seconds)."""
    payload = {"sub": username, "exp": int(time.time()) + ttl_minutes * 60}
    body = _b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64u_encode(sig)}"


def verify_token(token: str, secret: str) -> str | None:
    """Return the subject username when signature + expiry check out, else None."""
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64u_decode(sig), expected):
            return None
        payload = json.loads(_b64u_decode(body).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
            return None
        sub = payload.get("sub")
        return sub if isinstance(sub, str) and sub else None
    except (ValueError, json.JSONDecodeError, base64.binascii.Error):
        return None


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, settings: Settings = Depends(get_settings)) -> TokenOut:
    """Verify env-admin credentials (constant-time) and mint a bearer token."""
    if body.username != settings.admin_user or not hmac.compare_digest(
        body.password, settings.admin_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    return TokenOut(
        access_token=create_token(body.username, settings.secret_key, settings.token_ttl_minutes)
    )
