"""Post-RC item [101]: a retention policy for shared memory (and backup pruning).

Data lived forever until someone erased it item by item. A retention policy is
the missing *default*: plans that have not been reused for a configurable
period are removed, while the records that make a safety claim verifiable are
kept:

- **quarantined items stay** — a failed plan is the evidence behind "a failed
  plan cannot be auto-reused"; deleting it would delete the evidence;
- **consent history stays** — erasure already keeps it (`memory.consent` rows
  are separate from items), and retention follows the same rule;
- **the purge is audited** — the caller is expected to append one
  `memory.retention` record to the hash chain, so a deletion can be shown to
  have happened on purpose rather than by an attacker.

All functions here are pure selection logic over rows; the script does the I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from memory.states import ALL_STATES, QUARANTINED

#: States that may be purged, unless the caller asks for others.
PURGEABLE_STATES: tuple[str, ...] = tuple(state for state in ALL_STATES if state != QUARANTINED)

#: Default: a year of untouched reusable plans is enough for a demo, not a disk.
DEFAULT_MAX_AGE_DAYS = 365


@dataclass(frozen=True)
class RetentionPolicy:
    """What may be purged, and after how long."""

    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    states: tuple[str, ...] = PURGEABLE_STATES
    now: datetime | None = None

    def cutoff(self) -> datetime:
        """The oldest `updated_at` that survives this policy."""
        reference = self.now or datetime.now(tz=timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return reference - timedelta(days=max(0, int(self.max_age_days)))

    def allows(self, item: dict) -> bool:
        """True when one item (a row dict) may be purged."""
        if str(item.get("state", "")) not in self.states:
            return False
        stamp = str(item.get("updated_at", "") or item.get("created_at", ""))
        try:
            updated = datetime.fromisoformat(stamp)
        except ValueError:
            return False  # unreadable timestamp: keep it, never guess
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return updated < self.cutoff()


@dataclass(frozen=True)
class RetentionPlan:
    """The decision for one pass, before anything is deleted."""

    policy: RetentionPolicy
    purge_ids: tuple[str, ...]
    kept_by_reason: dict[str, int]

    @property
    def purge_count(self) -> int:
        return len(self.purge_ids)

    def as_dict(self) -> dict:
        return {
            "max_age_days": self.policy.max_age_days,
            "states": list(self.policy.states),
            "cutoff": self.policy.cutoff().isoformat(timespec="seconds"),
            "purge_count": self.purge_count,
            "purge_ids": list(self.purge_ids),
            "kept": dict(self.kept_by_reason),
        }


def plan_retention(items: list[dict], policy: RetentionPolicy) -> RetentionPlan:
    """Select the items a policy purges, and count why the others stay."""
    purge: list[str] = []
    kept: dict[str, int] = {}
    for item in items:
        if policy.allows(item):
            purge.append(str(item.get("id", "")))
            continue
        state = str(item.get("state", "UNKNOWN"))
        if state == QUARANTINED:
            reason = "quarantined"
        elif state not in policy.states:
            reason = f"state:{state}"
        else:
            reason = "recent"
        kept[reason] = kept.get(reason, 0) + 1
    return RetentionPlan(
        policy=policy,
        purge_ids=tuple(sorted(identifier for identifier in purge if identifier)),
        kept_by_reason=dict(sorted(kept.items())),
    )


def plan_backup_prune(snapshots: list[str], keep: int) -> tuple[str, ...]:
    """Names of the oldest snapshots beyond the newest ``keep`` (sorted names)."""
    ordered = sorted(snapshots)
    if keep < 0:
        raise ValueError("keep must be >= 0")
    return tuple(ordered[: max(0, len(ordered) - keep)])


__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "PURGEABLE_STATES",
    "RetentionPlan",
    "RetentionPolicy",
    "plan_backup_prune",
    "plan_retention",
]
