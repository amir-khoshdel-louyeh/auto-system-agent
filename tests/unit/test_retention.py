"""Post-RC item [101]: retention selects the right rows and nothing else.

Pure selection logic, so every rule is pinned here; the script that applies it
(and audits the purge) is covered in `tests/integration/test_ops_scripts.py`.
"""

import unittest
from datetime import datetime, timedelta, timezone

from memory.states import PUBLIC, QUARANTINED, SANDBOX
from storage.retention import (
    DEFAULT_MAX_AGE_DAYS,
    PURGEABLE_STATES,
    RetentionPolicy,
    plan_backup_prune,
    plan_retention,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def item(identifier: str, state: str, *, days_old: float, stamp: str = "") -> dict:
    moment = stamp or (NOW - timedelta(days=days_old)).isoformat()
    return {"id": identifier, "state": state, "updated_at": moment, "created_at": moment}


class RetentionPolicyTests(unittest.TestCase):
    def test_quarantined_items_are_never_purgeable_by_default(self):
        self.assertNotIn(QUARANTINED, PURGEABLE_STATES)
        plan = plan_retention([item("q", QUARANTINED, days_old=10_000)], RetentionPolicy(now=NOW))
        self.assertEqual(plan.purge_ids, ())
        self.assertEqual(plan.kept_by_reason, {"quarantined": 1})

    def test_old_reusable_items_are_purged_and_recent_ones_kept(self):
        plan = plan_retention(
            [
                item("old-public", PUBLIC, days_old=400),
                item("old-sandbox", SANDBOX, days_old=400),
                item("new-public", PUBLIC, days_old=10),
            ],
            RetentionPolicy(max_age_days=365, now=NOW),
        )
        self.assertEqual(plan.purge_ids, ("old-public", "old-sandbox"))
        self.assertEqual(plan.purge_count, 2)
        self.assertEqual(plan.kept_by_reason, {"recent": 1})

    def test_the_cutoff_is_inclusive_of_the_boundary_age(self):
        policy = RetentionPolicy(max_age_days=30, now=NOW)
        exactly = item("edge", PUBLIC, days_old=30)
        self.assertFalse(policy.allows(exactly), "an item exactly at the cutoff survives")
        self.assertTrue(policy.allows(item("older", PUBLIC, days_old=30.01)))

    def test_an_unreadable_timestamp_is_kept_rather_than_guessed(self):
        plan = plan_retention(
            [item("broken", PUBLIC, days_old=0, stamp="not-a-date")],
            RetentionPolicy(now=NOW),
        )
        self.assertEqual(plan.purge_ids, ())
        self.assertEqual(plan.kept_by_reason, {"recent": 1})

    def test_a_custom_state_list_limits_what_may_be_purged(self):
        plan = plan_retention(
            [
                item("old-public", PUBLIC, days_old=400),
                item("old-sandbox", SANDBOX, days_old=400),
            ],
            RetentionPolicy(max_age_days=365, states=(PUBLIC,), now=NOW),
        )
        self.assertEqual(plan.purge_ids, ("old-public",))
        self.assertEqual(plan.kept_by_reason, {"state:SANDBOX": 1})

    def test_the_report_is_json_ready_and_states_the_policy(self):
        report = plan_retention(
            [item("old", PUBLIC, days_old=400)], RetentionPolicy(now=NOW)
        ).as_dict()
        self.assertEqual(
            set(report), {"max_age_days", "states", "cutoff", "purge_count", "purge_ids", "kept"}
        )
        self.assertEqual(report["max_age_days"], DEFAULT_MAX_AGE_DAYS)
        self.assertIn("2025-10-18", report["cutoff"])

    def test_an_empty_store_is_an_empty_plan(self):
        plan = plan_retention([], RetentionPolicy(now=NOW))
        self.assertEqual(plan.purge_count, 0)
        self.assertEqual(plan.kept_by_reason, {})


class BackupPruneTests(unittest.TestCase):
    NAMES = [
        "app-20261016T000000Z.db",
        "app-20261017T000000Z.db",
        "app-20261018T000000Z.db",
    ]

    def test_the_oldest_snapshots_go_and_the_newest_stay(self):
        self.assertEqual(
            plan_backup_prune(self.NAMES, 2),
            ("app-20261016T000000Z.db",),
        )
        self.assertEqual(plan_backup_prune(self.NAMES, 0), tuple(self.NAMES))
        self.assertEqual(plan_backup_prune(self.NAMES, 5), ())

    def test_selection_is_order_independent(self):
        shuffled = list(reversed(self.NAMES))
        self.assertEqual(plan_backup_prune(shuffled, 1), plan_backup_prune(self.NAMES, 1))

    def test_a_negative_keep_is_refused(self):
        with self.assertRaises(ValueError):
            plan_backup_prune(self.NAMES, -1)


if __name__ == "__main__":
    unittest.main()
