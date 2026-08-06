#!/usr/bin/env bash
# Post-RC items [100]-[103]: the nightly operations job, in one command.
#
# Every step is the same script an operator runs by hand, in the order that
# makes the evidence trustworthy:
#
#   1. backup   — consistent SQLite snapshot + manifest (hash, migrations,
#                 row counts, every audit chain verified);
#   2. verify   — re-check the snapshot that was just written;
#   3. prune    — keep the newest N snapshots (never the last one);
#   4. retention— purge expired *reusable* memory items, audit the purge;
#   5. anchor   — publish a digest of every audit chain head (copy the
#                 anchors/ directory off-host; that is what makes a full chain
#                 rewrite detectable).
#
# A failure stops the job at that step (`set -euo pipefail`), so a broken
# backup never gets pruned and a failed purge is never hidden by an anchor.
#
# Usage (all optional, defaults shown):
#   ASA_DB=data/app.db ASA_BACKUP_DIR=backups ASA_BACKUP_KEEP=14 \
#   ASA_RETENTION_DAYS=365 ASA_ANCHOR_DIR=data/anchors \
#   ASA_OFFSITE_DIR=/mnt/offsite/asa \
#   scripts/ops_job.sh
#
# Scheduling: deploy/systemd/asa-ops.{service,timer} (daily 02:30 UTC), or the
# cron line in docs/DEPLOYMENT.md §3.
set -euo pipefail

cd "$(dirname "$0")/.."

DB="${ASA_DB:-data/app.db}"
BACKUP_DIR="${ASA_BACKUP_DIR:-backups}"
KEEP="${ASA_BACKUP_KEEP:-14}"
DAYS="${ASA_RETENTION_DAYS:-365}"
ANCHOR_DIR="${ASA_ANCHOR_DIR:-data/anchors}"
OFFSITE="${ASA_OFFSITE_DIR:-}"

echo "ops job start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "[1/5] backup"
if [[ -n "$OFFSITE" ]]; then
  python scripts/backup.py backup --db "$DB" --out "$BACKUP_DIR" --copy-to "$OFFSITE"
else
  python scripts/backup.py backup --db "$DB" --out "$BACKUP_DIR"
fi

echo "[2/5] verify"
latest="$(find "$BACKUP_DIR" -maxdepth 1 -name '*.db' -type f -printf '%T@ %p\n' \
  | sort -nr | head -n 1 | cut -d' ' -f2-)"
if [[ -z "$latest" ]]; then
  echo "error: no snapshot was produced in $BACKUP_DIR" >&2
  exit 1
fi
python scripts/backup.py verify "$latest"

echo "[3/5] prune (keep $KEEP)"
python scripts/backup.py prune --out "$BACKUP_DIR" --keep "$KEEP"

echo "[4/5] retention (max_age_days=$DAYS)"
# A database that does not exist yet (first boot) is not a retention failure.
if [[ -f "$DB" ]]; then
  python scripts/retention.py --db "$DB" --days "$DAYS"
else
  echo "  skipped: no database at $DB"
fi

echo "[5/5] audit anchor"
if [[ -f "$DB" ]]; then
  python scripts/anchor_audit.py anchor --db "$DB" --out "$ANCHOR_DIR"
else
  echo "  skipped: no database at $DB"
fi

echo "ops job done: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "next: copy $ANCHOR_DIR and the newest snapshot off the host (see docs/DEPLOYMENT.md §3)"
