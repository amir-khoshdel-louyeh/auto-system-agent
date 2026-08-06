#!/usr/bin/env python3
"""Post-RC item [103]: anchor every audit chain head, then verify against it.

`verify_chain` catches a tampered row, a deletion and a relink. It cannot catch
an attacker who rewrites a whole chain consistently — the chain still verifies,
the head hash simply changed. This script closes that gap the boring way:

    anchor   one digest over every chain head + event count, written as a JSON
             document that is meant to be copied off the host (and HMAC-signed
             with ASA_ANCHOR_KEY when the operator sets one);
    verify   recompute the digest from the database and compare. A rewrite of
             any chain, an added chain or a removed chain shows up here even
             though `verify_chain` would pass.

The document deliberately contains only (execution_id, events, head_hash) — no
payloads, no paths, nothing that leaks what ran.

Usage::

    python scripts/anchor_audit.py anchor --db data/app.db --out data/anchors
    python scripts/anchor_audit.py verify --db data/app.db \\
        --anchor data/anchors/audit-anchor-2026-10-18.json
    ASA_ANCHOR_KEY=... python scripts/anchor_audit.py anchor --db data/app.db --out data/anchors
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _key(explicit: str = "") -> str:
    import os

    from storage.anchor import ANCHOR_KEY_ENV

    return explicit or os.environ.get(ANCHOR_KEY_ENV, "").strip()


def write_anchor(
    db: str | Path, out_dir: str | Path, *, key: str = "", name: str = "", now: str = ""
) -> tuple[Path, dict]:
    """Write one anchor document for ``db``; returns ``(path, document)``."""
    from storage.anchor import build_document, chain_heads
    from storage.audit import AuditStore

    heads = chain_heads(AuditStore(db))
    stamp = now or datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    document = build_document(heads, key=key, created_at=now)
    target = Path(out_dir) / (name or f"audit-anchor-{stamp}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target, document


def verify_anchor(db: str | Path, anchor: str | Path, *, key: str = "") -> dict:
    """Verify a stored anchor against the database; returns the report."""
    from storage.anchor import chain_heads, load_document, verify_document
    from storage.audit import AuditStore

    document = load_document(Path(anchor).read_text(encoding="utf-8"))
    check = verify_document(document, chain_heads(AuditStore(db)), key=key)
    return {
        "ok": check.ok,
        "reason": check.reason,
        "chains": check.chains,
        "anchored_chains": int(document.get("chains", 0)),
        "digest": check.digest,
        "expected_digest": check.expected,
        "signed": bool(document.get("signed")),
        "signature_ok": check.signature_ok,
        "created_at": str(document.get("created_at", "")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="anchor and verify the audit chain heads")
    parser.add_argument("command", choices=("anchor", "verify"))
    parser.add_argument("--db", required=True, help="sqlite database path")
    parser.add_argument("--out", default="data/anchors", help="anchor output directory")
    parser.add_argument("--anchor", default="", help="anchor document to verify")
    parser.add_argument("--name", default="", help="explicit anchor file name")
    parser.add_argument("--key", default="", help="signing key (default: ASA_ANCHOR_KEY)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    key = _key(args.key)
    if args.command == "anchor":
        target, document = write_anchor(args.db, args.out, key=key, name=args.name)
        report = {
            "anchor": str(target),
            "chains": document["chains"],
            "events": document["events"],
            "digest": document["digest"],
            "signed": document["signed"],
        }
    else:
        if not args.anchor:
            print("error: verify needs --anchor", file=sys.stderr)
            return 2
        report = verify_anchor(args.db, args.anchor, key=key)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for name, value in report.items():
            print(f"{name:>16}: {value}")
    if args.command == "verify" and not report["ok"]:
        print(f"anchor verification FAILED: {report['reason']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
