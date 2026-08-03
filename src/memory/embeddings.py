"""Phase 7 item [66]: deterministic local embeddings (no model, no network).

Vectors come from *signed feature hashing* over normalized tokens with
SHA-256, so they are stable across processes and machines (Python's built-in
``hash`` is randomised and must never be used here). The embedding is weak as
semantics but strong as engineering: reusable retrieval, ranking and filters
can be tested deterministically, and a real embedding provider can replace
:func:`embed` later behind the same interface.

Vectors are unit-length, which makes the dot product the cosine similarity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

#: Frozen embedding identity, stored with every item.
EMBEDDING_MODEL = "hashing-v1"
DIM = 256

_WORD = re.compile(r"[a-z0-9_./-]+")


def tokenize(text: str) -> list[str]:
    """Normalized tokens (lowercase, alphanumeric plus path characters)."""
    return _WORD.findall(str(text or "").lower())


def _bucket(token: str) -> tuple[int, float]:
    """Stable (index, sign) for one token — SHA-256, never `hash()`."""
    digest = hashlib.sha256(f"{EMBEDDING_MODEL}:{token}".encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % DIM
    sign = 1.0 if digest[4] % 2 == 0 else -1.0
    return index, sign


def embed(text: str) -> list[float]:
    """Unit-length hashing embedding of ``text`` (zero vector when empty)."""
    vector = [0.0] * DIM
    for token in tokenize(text):
        index, sign = _bucket(token)
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity of two vectors (0.0 when either is empty/zero)."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def similarity(left: str, right: str) -> float:
    """Cosine similarity of two texts under this embedding."""
    return cosine(embed(left), embed(right))


def serialize(vector: list[float]) -> str:
    """Compact JSON form stored in `memory_items.embedding`."""
    return json.dumps([round(value, 6) for value in vector], separators=(",", ":"))


def deserialize(payload: str) -> list[float]:
    """Parse a stored embedding (empty/garbage yields a zero vector)."""
    try:
        raw = json.loads(payload) if payload else []
    except (TypeError, ValueError):
        return [0.0] * DIM
    if not isinstance(raw, list) or len(raw) != DIM:
        return [0.0] * DIM
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return [0.0] * DIM


def describe() -> dict[str, Any]:
    """Embedding identity for reports and stored metadata."""
    return {"model": EMBEDDING_MODEL, "dim": DIM}
