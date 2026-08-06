"""Post-RC item [103]: anchor the audit chains so a full rewrite is detectable.

`AuditStore.verify_chain` catches a tampered row, a deletion and a relink — but
an attacker who rewrites *every* row of a chain, hashes included, produces a
chain that verifies. The residual risk was named in `docs/LIMITATIONS.md` §2 and
is closed here the way it is closed in practice: publish a digest of every
chain head, and keep it somewhere the attacker does not control.

This module is the pure part: build the head list from a store, reduce it to one
canonical digest, sign the document, and verify both later. Writing it to a file
(and copying it off-host) is the caller's job — see `scripts/anchor_audit.py`.

The digest covers `(execution_id, head_hash, events)` sorted by execution id, so
it is stable across runs and changes if a chain is edited, extended or removed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#: Document shape version (bump if the digest inputs ever change).
ANCHOR_VERSION = "anchor-v1"

#: Environment variable holding the signing key; unset means "unsigned".
ANCHOR_KEY_ENV = "ASA_ANCHOR_KEY"


def canonical(document: Any) -> str:
    """Canonical JSON (sorted keys) — the bytes that get hashed or signed."""
    return json.dumps(document, sort_keys=True, default=str, separators=(",", ":"))


def chain_heads(store) -> list[dict[str, Any]]:
    """``[{execution_id, events, head_hash}]`` for every chain in the store."""
    heads: list[dict[str, Any]] = []
    for execution_id in store.execution_ids():
        rows = store.events(execution_id)
        heads.append(
            {
                "execution_id": execution_id,
                "events": len(rows),
                "head_hash": rows[-1]["hash"] if rows else "",
            }
        )
    return sorted(heads, key=lambda head: head["execution_id"])


def digest(heads: list[dict[str, Any]]) -> str:
    """One digest over every chain head (stable, order-independent by sorting)."""
    body = canonical([[head["execution_id"], head["events"], head["head_hash"]] for head in heads])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def sign(document: dict[str, Any], key: str) -> str:
    """HMAC-SHA256 of the canonical body (without the signature field)."""
    body = {name: value for name, value in document.items() if name != "signature"}
    return hmac.new(
        key.encode("utf-8"), canonical(body).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_document(
    heads: list[dict[str, Any]],
    *,
    key: str = "",
    created_at: str = "",
) -> dict[str, Any]:
    """The anchor document: heads, digest, and an optional HMAC signature."""
    timestamp = created_at or datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    document: dict[str, Any] = {
        "anchor_version": ANCHOR_VERSION,
        "created_at": timestamp,
        "chains": len(heads),
        "events": sum(int(head["events"]) for head in heads),
        "digest": digest(heads),
        "heads": heads,
        "signed": bool(key),
    }
    if key:
        document["signature"] = sign(document, key)
    return document


@dataclass(frozen=True)
class AnchorCheck:
    """Outcome of comparing a stored anchor with the chains on disk."""

    ok: bool
    reason: str = ""
    digest: str = ""
    expected: str = ""
    chains: int = 0
    signature_ok: bool | None = None


def verify_document(
    document: dict[str, Any], heads: list[dict[str, Any]], *, key: str = ""
) -> AnchorCheck:
    """Recompute the digest (and signature) and compare with a stored anchor."""
    if str(document.get("anchor_version", "")) != ANCHOR_VERSION:
        return AnchorCheck(ok=False, reason="anchor version mismatch", digest=digest(heads))
    expected = str(document.get("digest", ""))
    actual = digest(heads)
    signature_ok: bool | None = None
    if document.get("signed"):
        if not key:
            return AnchorCheck(
                ok=False,
                reason=f"anchor is signed but {ANCHOR_KEY_ENV} is not set",
                digest=actual,
                expected=expected,
                chains=len(heads),
            )
        signature_ok = hmac.compare_digest(str(document.get("signature", "")), sign(document, key))
        if not signature_ok:
            return AnchorCheck(
                ok=False,
                reason="anchor signature does not match: the document itself was altered",
                digest=actual,
                expected=expected,
                chains=len(heads),
                signature_ok=False,
            )
    if actual != expected:
        recorded = {str(head["execution_id"]): head for head in document.get("heads", [])}
        current = {str(head["execution_id"]): head for head in heads}
        added = sorted(set(current) - set(recorded))
        removed = sorted(set(recorded) - set(current))
        changed = sorted(
            identifier
            for identifier in set(recorded) & set(current)
            if recorded[identifier] != current[identifier]
        )
        detail = []
        if added:
            detail.append(f"new chains: {added[:3]}")
        if removed:
            detail.append(f"missing chains: {removed[:3]}")
        if changed:
            detail.append(f"rewritten chains: {changed[:3]}")
        return AnchorCheck(
            ok=False,
            reason="chain heads changed since the anchor: "
            + ("; ".join(detail) or "digest differs"),
            digest=actual,
            expected=expected,
            chains=len(heads),
            signature_ok=signature_ok,
        )
    return AnchorCheck(
        ok=True,
        digest=actual,
        expected=expected,
        chains=len(heads),
        signature_ok=signature_ok,
    )


def load_document(text: str) -> dict[str, Any]:
    """Parse an anchor document, raising ValueError on a non-object."""
    document = json.loads(text)
    if not isinstance(document, dict):
        raise ValueError("anchor document must be a JSON object")
    return document


__all__ = [
    "ANCHOR_KEY_ENV",
    "ANCHOR_VERSION",
    "AnchorCheck",
    "build_document",
    "canonical",
    "chain_heads",
    "digest",
    "load_document",
    "sign",
    "verify_document",
]
