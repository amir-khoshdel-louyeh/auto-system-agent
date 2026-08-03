"""Shared helpers for the memory package (ids, timestamps, fingerprinting).

Kept separate so every memory module uses the same id shape and the same
canonical fingerprint as the workflow/version layer (SHA-256 hex).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any


def new_id() -> str:
    """Random TEXT primary key (matches `app.models.new_id`)."""
    return uuid.uuid4().hex


def utcnow() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    """Canonical JSON of a plan (sorted keys) — the bytes we fingerprint."""
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    """Stable content hash of a canonical plan (one item per fingerprint)."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
