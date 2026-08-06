#!/usr/bin/env python3
"""Post-RC item [101]: apply the shared-memory retention policy and audit it.

Memory kept plans forever until an operator erased them one by one. This script
is the missing default job:

1. select the items a policy may purge (`storage.retention.plan_retention`) —
   quarantined items and recent items are never selected, and a row with an
   unreadable timestamp is kept rather than guessed at;
2. unless `--dry-run`, delete exactly those items with `MemoryStore.erase`,
   which also removes their events and leaves the consent history in place;
3. append one `memory.retention` record to the audit chain under the synthetic
   execution id ``retention:<timestamp>``, so a deletion is *verifiable* and
   cannot be confused with tampering.

Usage::

    python scripts/retention.py --db data/app.db --days 180 --dry-run
    python scripts/retention.py --db data/app.db --days 180
    python scripts/retention.py --db data/app.db --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _stores(db: str | Path):
    from memory.store import MemoryStore
    from storage.audit import AuditStore

    return MemoryStore(db), AuditStore(db)


def run_retention(
    db: str | Path,
    *,
    days: int,
    states: tuple[str, ...] | None = None,
    dry_run: bool = False,
    now: str = "",
) -> dict:
    """Apply the policy once; returns the report (also printed by the CLI)."""
    from storage.retention import RetentionPolicy, plan_retention

    store, audit = _stores(db)
    policy = RetentionPolicy(
        max_age_days=days,
        **({"states": states} if states else {}),
        now=datetime.fromisoformat(now) if now else None,
    )
    rows = [item.as_dict() for item in store.list_items(limit=10_000)]
    plan = plan_retention(rows, policy)
    purged: list[str] = []
    if not dry_run:
        for item_id in plan.purge_ids:
            if store.erase(item_id):
                purged.append(item_id)
        if purged:
            stamp = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
            from storage.audit import MEMORY_RETENTION

            audit.append(
                f"retention:{stamp}",
                MEMORY_RETENTION,
                {
                    "policy": {
                        "max_age_days": policy.max_age_days,
                        "states": list(policy.states),
                        "cutoff": policy.cutoff().isoformat(timespec="seconds"),
                    },
                    "purged": len(purged),
                    "kept": plan.kept_by_reason,
                    "item_ids": purged[:50],
                },
            )
    return {
        "dry_run": dry_run,
        "database": str(db),
        **plan.as_dict(),
        "purged_count": len(purged),
        "audited": bool(purged) and not dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="purge expired shared-memory items")
    parser.add_argument("--db", default="data/app.db", help="sqlite database path")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="purge items untouched for this many days (default: policy default)",
    )
    parser.add_argument(
        "--state",
        action="append",
        default=None,
        help="state that may be purged (repeatable); quarantined items are never purged",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without deleting")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    from storage.retention import DEFAULT_MAX_AGE_DAYS

    report = run_retention(
        args.db,
        days=args.days if args.days is not None else DEFAULT_MAX_AGE_DAYS,
        states=tuple(args.state) if args.state else None,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        mode = "DRY RUN — nothing deleted" if report["dry_run"] else "applied"
        print(f"retention {mode}")
        print(f"  policy      : max_age_days={report['max_age_days']} states={report['states']}")
        print(f"  cutoff      : {report['cutoff']}")
        print(f"  purgeable   : {report['purge_count']}")
        print(f"  purged      : {report['purged_count']}")
        print(f"  kept        : {report['kept']}")
        print(f"  audited     : {report['audited']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
