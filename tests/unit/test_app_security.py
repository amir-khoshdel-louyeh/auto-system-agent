"""Phase 1 item [11]: password hashing + short-lived JWT (FR-01).

Adversarial cases included: expired/future-dated tokens, tampered
segments, wrong secret, ``alg=none`` downgrade and signature stripping.
"""

import time
import unittest

from app.auth import create_token, verify_token
from app.security import (
    TokenError,
    decode_token,
    encode_token,
    hash_password,
    verify_password,
)

SECRET = "unit-test-secret"


class PasswordTests(unittest.TestCase):
    def test_roundtrip_ok(self):
        stored = hash_password("s3cret-pass")
        self.assertTrue(verify_password("s3cret-pass", stored))

    def test_wrong_password_rejected(self):
        stored = hash_password("s3cret-pass")
        self.assertFalse(verify_password("other-pass", stored))
        self.assertFalse(verify_password("", stored))

    def test_salt_is_per_password(self):
        first = hash_password("same-pass")
        second = hash_password("same-pass")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("same-pass", first))
        self.assertTrue(verify_password("same-pass", second))

    def test_format_is_self_describing(self):
        stored = hash_password("s3cret-pass")
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        self.assertEqual(scheme, "pbkdf2_sha256")
        self.assertGreaterEqual(int(iterations), 100_000)
        self.assertEqual(len(salt_hex), 32)
        self.assertEqual(len(digest_hex), 64)

    def test_malformed_storages_fail_closed(self):
        for bad in ("", "nonsense", "a$b$c", "pbkdf2_sha256$0$$", None):  # type: ignore[arg-type]
            self.assertFalse(verify_password("x", bad))


class JwtTests(unittest.TestCase):
    def _claims(self, **extra):
        payload = {"sub": "amir", **extra}
        return encode_token(payload, SECRET, ttl_seconds=60)

    def test_roundtrip_returns_claims(self):
        claims = decode_token(self._claims(role="OWNER"), SECRET)
        self.assertEqual(claims["sub"], "amir")
        self.assertEqual(claims["role"], "OWNER")
        self.assertGreater(claims["exp"], time.time())

    def test_three_segment_jwt_shape(self):
        self.assertEqual(len(self._claims().split(".")), 3)

    def test_expired_token_rejected(self):
        token = encode_token({"sub": "amir"}, SECRET, ttl_seconds=60, now=time.time() - 3600)
        with self.assertRaises(TokenError):
            decode_token(token, SECRET)

    def test_future_iat_rejected(self):
        token = encode_token({"sub": "amir"}, SECRET, ttl_seconds=60, now=time.time() + 3600)
        with self.assertRaises(TokenError):
            decode_token(token, SECRET)

    def test_wrong_secret_rejected(self):
        with self.assertRaises(TokenError):
            decode_token(self._claims(), "other-secret")

    def test_tampered_payload_rejected(self):
        head, body, sig = self._claims().split(".")
        tampered = f"{head}.{body}x.{sig}"
        with self.assertRaises(TokenError):
            decode_token(tampered, SECRET)

    def test_alg_none_downgrade_rejected(self):
        import base64
        import json

        def b64u(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        head = b64u(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        body = b64u(json.dumps({"sub": "amir", "exp": int(time.time()) + 60}).encode())
        with self.assertRaises(TokenError):
            decode_token(f"{head}.{body}.", SECRET)

    def test_missing_segments_rejected(self):
        for bad in ("", "one.two", "a.b.c.d"):
            with self.assertRaises(TokenError):
                decode_token(bad, SECRET)

    def test_auth_helpers_short_lived_and_fail_closed(self):
        token = create_token("amir", SECRET, ttl_minutes=5)
        self.assertEqual(verify_token(token, SECRET), "amir")
        self.assertIsNone(verify_token(token, "other"))
        self.assertIsNone(verify_token(token + "z", SECRET))
        self.assertIsNone(verify_token("", SECRET))
        with self.assertRaises(ValueError):
            create_token("amir", SECRET, ttl_minutes=0)


if __name__ == "__main__":
    unittest.main()
