"""Phase 1 item [11]: password hashing and short-lived JWT (FR-01).

Stdlib-only implementations, deliberately project-owned so the security
core stays auditable:

- ``hash_password`` / ``verify_password``: PBKDF2-HMAC-SHA256 with a fresh
  random salt per password, stored as ``pbkdf2_sha256$iterations$salt$hex``.
  Verification is constant-time and never stores or logs plaintext.
- ``encode_token`` / ``decode_token``: real JWT (RFC 7519 shape):
  ``base64url(header).base64url(payload).base64url(signature)`` signed with
  HS256. ``decode_token`` pins the ``alg`` header (rejects ``none`` and any
  non-HS256 algorithm), verifies the signature with ``hmac.compare_digest``
  and enforces ``exp``/``iat``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Any

PBKDF2_ITERATIONS = 100_000
PASSWORD_SCHEME = "pbkdf2_sha256"
_SALT_BYTES = 16
_ALGORITHM = "HS256"


class TokenError(Exception):
    """Raised by decode_token for any malformed/invalid/expired token."""


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return ``pbkdf2_sha256$iterations$salt_hex$hexdigest`` for one password."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PASSWORD_SCHEME}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored verifier string."""
    try:
        scheme, iterations_s, salt_hex, digest_hex = stored.split("$")
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    if iterations < 1 or not salt or not expected:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------
# JWT (HS256)
# --------------------------------------------------------------------------
def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def encode_token(
    payload: dict[str, Any],
    secret: str,
    ttl_seconds: int,
    *,
    now: float | None = None,
) -> str:
    """Sign ``payload`` as an HS256 JWT with ``iat``/``exp`` stamped in."""
    if ttl_seconds < 1:
        raise ValueError("ttl_seconds must be >= 1 (short-lived tokens only)")
    issued = int(now if now is not None else time.time())
    claims = dict(payload)
    claims.setdefault("iat", issued)
    claims["exp"] = issued + ttl_seconds
    header = {"alg": _ALGORITHM, "typ": "JWT"}
    head = _b64u_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body = _b64u_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{head}.{body}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    return f"{signing_input}.{_b64u_encode(signature.digest())}"


def decode_token(
    token: str,
    secret: str,
    *,
    now: float | None = None,
    leeway_seconds: int = 0,
) -> dict[str, Any]:
    """Validate signature, algorithm, expiry and issued-at; return the claims.

    Any failure raises :class:`TokenError` - callers fail closed (never
    treat a bad token as anonymous-but-accepted).
    """
    try:
        head, body, signature = token.split(".")
    except ValueError as exc:
        raise TokenError("token must have three segments") from exc
    try:
        header = json.loads(_b64u_decode(head))
        claims = json.loads(_b64u_decode(body))
        provided = _b64u_decode(signature)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise TokenError("token segments are not valid base64url JSON") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError("token header and payload must be JSON objects")
    if header.get("alg") != _ALGORITHM or header.get("typ") != "JWT":
        raise TokenError(f"unsupported token algorithm: {header.get('alg')!r}")
    signing_input = f"{head}.{body}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(provided, expected):
        raise TokenError("signature mismatch")
    current = int(now if now is not None else time.time())
    exp = claims.get("exp")
    iat = claims.get("iat")
    if not isinstance(exp, int):
        raise TokenError("missing integer exp claim")
    if exp - leeway_seconds < current:
        raise TokenError("token expired")
    if isinstance(iat, int) and iat - leeway_seconds > current:
        raise TokenError("token issued in the future")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenError("missing sub claim")
    return claims
