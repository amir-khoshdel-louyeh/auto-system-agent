# Deployment runbook (and what was verified)

**Items:** Phase 9 — [88] Verify clean compose deployment from a new machine,
[83] production configuration, [89] backup/restore and migration upgrades.
**Verification run:** 2026-10-18 on a Linux host with Docker 29.9.0 and
Compose v5.6.0, from a clean build (`docker compose up --build` with no
cached images for this project), using throw-away project names
(`asa-verify`, `asa-verify-prod`) so no existing volume was touched.

---

## 1. Two deployment files

| File | Purpose | Defaults |
|---|---|---|
| `compose.yml` | developer stack: api + postgres/pgvector + redis | every value has a dev default, so `docker compose up --build` works from a clone |
| `compose.prod.yml` | production stack, same topology | **no** credential has a fallback: every one uses `${VAR:?message}`, Redis requires a password, only the API is published (on loopback) |

Command for production:

```bash
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export APP_ADMIN_USER=admin APP_ADMIN_PASSWORD=... \
       POSTGRES_USER=agent POSTGRES_PASSWORD=... POSTGRES_DB=agent \
       REDIS_PASSWORD=...
docker compose -f compose.prod.yml up -d --build
```

## 2. What the verification actually showed

### 2.1 Developer stack, clean build

| Check | Command | Result |
|---|---|---|
| All services healthy | `docker compose -p asa-verify -f compose.yml ps` | `api Up (healthy)`, `db Up (healthy)`, `redis Up (healthy)` |
| Health endpoint | `curl -s localhost:8000/health` | `200 {"status":"healthy","version":"0.1.0"}` |
| Login | `POST /auth/login` (env admin) | `200`, JWT issued (167 chars) |
| Authenticated read | `GET /executions` with that token | `200 {"total":0,"executions":[]}` |
| Failure closed | `GET /executions` without a token | `401` |
| Migrations applied in a fresh container | `docker compose exec api python -c "from app.db import migration_status; ..."` | `001 init`, `002 audit`, `003 user_roles`, `004 workspaces`, `005 workflow_runs`, `006 memory`, `007 memory_usage` — all `APPLIED`, no `DRIFT` |
| Login throttle live | 12 × `POST /auth/login` with a wrong password | `401` ×10, then `429` with `retry-after: 6` |
| Runs unprivileged | `docker compose exec api id` | `uid=10001(app) gid=10001(app)` |

### 2.2 State survives a container rebuild (fix found by this verification)

The first pass exposed a real defect: the SQLite database (users, runs,
approvals, audit chain) lived in the container's writable layer, so
`up --force-recreate` — or any redeploy — silently discarded all state.
`compose.yml` and `compose.prod.yml` now mount a named volume at
`/code/data`, and `Dockerfile.api` creates `/code/data` owned by the
unprivileged `app` user before dropping to it (a fresh named volume inherits
that ownership).

Verified after the fix:

```
docker compose ... up -d --build           # fresh stack
POST /admin/users {"username":"persist-check", ...}   -> 201
GET  /admin/users                          -> ['persist-check']
docker compose ... up -d --force-recreate  # container replaced
GET  /admin/users                          -> ['persist-check']
```

Before the change the same sequence could not have worked: `git show
HEAD:compose.yml` contains no volume for `/code/data` (only `pgdata` and
`redisdata`), so the file had nowhere durable to live.

### 2.3 Production stack

| Check | Result |
|---|---|
| Start without the secrets | refused: `required variable POSTGRES_PASSWORD is missing a value: POSTGRES_PASSWORD must be set for production` (exit 1) — nothing starts |
| Start with generated secrets | `api`, `db`, `redis` all healthy; `GET /health` → `200`; login `200` |
| Environment inside the container | `ENVIRONMENT` = `production`, `SECRET_KEY` replaced, `LOG_LEVEL` = `INFO` (dev secrets are rejected by `Settings.validate`) |
| Exposure | `docker ps` shows only `127.0.0.1:8000->8000/tcp` for the API; `db` and `redis` show no published port |
| Logging | container logs contain the JSON access line, e.g. `{"logger": "app.access", "message": "POST /auth/login -> 200", "http": {"method": "POST", "path": "/auth/login", "status": 200, "duration_ms": 1.487, "client": "79f1ed7c3831"}}` — no headers, no body, peer address as a digest |

## 3. Operating it

### Backup, off-site copy and retention (Phase 9 item [89], post-RC [101]-[103])

```bash
python scripts/backup.py backup --db data/app.db --out backups/ --name nightly \
    --copy-to /mnt/offsite/asa
python scripts/backup.py verify backups/nightly.db
python scripts/backup.py prune --out backups/ --keep 14 --dry-run
python scripts/retention.py --db data/app.db --days 365 --dry-run
```

The snapshot is taken with SQLite's online-backup API (consistent while the
API is writing) and accompanied by `nightly.db.manifest.json`: SHA-256, size,
every applied migration with its checksum, table row counts, and the result
of verifying **every** audit chain in the copy. `verify` re-checks all of it.
`--copy-to` writes the snapshot **and** its manifest to a second directory
(a network mount or an SSHFS path) and re-hashes the copy, so a truncated
transfer fails at backup time rather than at restore time. For a remote host,
mount it, or push the directory afterwards:

```bash
rsync -a --delete backups/ backup-host:/srv/asa-backups/
```

`prune` keeps the newest `--keep` snapshots and removes older ones together
with their manifests; it refuses `--keep 0`, because the newest snapshot is
the last copy. `retention.py` is the separate data-lifecycle job for shared
memory: it purges **SANDBOX/PUBLIC** items untouched for `--days`, never a
quarantined item (that row is the evidence a failed plan cannot be reused),
keeps the consent history, and appends one `memory.retention` record to the
audit chain so a deletion is provably intentional. Both commands support
`--dry-run`; run them that way first.

### Scheduled operations job (post-RC [101])

`scripts/ops_job.sh` runs the five steps in order and stops at the first
failure, so a broken backup is never pruned and a failed purge is never
hidden by an anchor:

```
backup → verify → prune → retention → anchor
```

```bash
sudo cp deploy/systemd/asa-ops.service deploy/systemd/asa-ops.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now asa-ops.timer     # daily 02:30 UTC, Persistent=true
```

Adjust `WorkingDirectory`, `User` and the `Environment=` lines in the unit
to the checkout (the shipped values assume `/srv/auto-system-agent`). Set
`ASA_OFFSITE_DIR` to the mounted off-host target. The equivalent cron line:

```cron
30 2 * * *  cd /srv/auto-system-agent && ASA_OFFSITE_DIR=/mnt/offsite/asa scripts/ops_job.sh
```

### Audit anchoring (post-RC [103])

`verify_chain` catches an edited row, a deletion and a relink. It cannot catch
an attacker who rewrites **every** row consistently; the chain still verifies,
only its head changed. The job therefore writes an anchor document daily:

```bash
python scripts/anchor_audit.py anchor --db data/app.db --out data/anchors \
    --key "$ASA_ANCHOR_KEY"
python scripts/anchor_audit.py verify --db data/app.db \
    --anchor data/anchors/audit-anchor-2026-10-18T023000Z.json \
    --key "$ASA_ANCHOR_KEY"
```

The document holds one `(execution_id, events, head_hash)` row per chain, one
SHA-256 over all of them, and an HMAC-SHA256 signature when `ASA_ANCHOR_KEY`
is set. It contains no payloads and no paths. **Copy `data/anchors/` off the
host**: an anchor stored beside the database protects nothing. `verify` exits
1 and names the changed chains when a head no longer matches.

### Metrics scraping (post-RC [100])

`GET /metrics/prometheus` returns Prometheus text exposition v0.0.4 with the
same aggregates as `GET /metrics/executions`, plus memory item counts and a
build-info sample. It requires `audit:read` (a valid JWT), like the other
read endpoints:

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
    -H 'content-type: application/json' \
    -d '{"username":"admin","password":"..."}' | python -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/metrics/prometheus
```

Families: `asa_runs_total`, `asa_runs_succeeded_total`, `asa_runs_failed_total`,
`asa_run_success_rate`, `asa_run_latency_average_milliseconds`,
`asa_run_latency_p95_milliseconds`, `asa_step_retries_total`,
`asa_approval_requests_total`, `asa_approval_average_seconds`,
`asa_memory_items{state=...}`, `asa_memory_reuse_hits_total`,
`asa_memory_reuse_hit_rate`, `asa_build_info`. A CI job runs the same
benchmark with `--api-only` on every push and fails when worst p95 reaches
the 300 ms NFR budget (`scripts/benchmark.py --max-p95`).

### Restore

```bash
docker compose stop api
python scripts/backup.py restore backups/nightly.db --db data/app.db --force
docker compose start api
```

`restore` refuses to overwrite an existing database without `--force` and
refuses a snapshot whose hash, row counts or audit chains do not match its
manifest — so a corrupt or tampered copy cannot be installed silently.

In compose, `data/app.db` lives in the `apidata` volume:

```bash
docker compose cp backups/nightly.db api:/code/data/app.db   # or back up the volume
```

### Migration upgrade

Migrations are applied automatically at startup by the checksum-tracked
runner. An older database upgrades in place: newer files apply, existing rows
and audit chains survive, re-running is a no-op, and editing an
already-applied migration is refused as drift (proven in
`tests/integration/test_backup_restore.py::MigrationUpgradeTests`).

```bash
docker compose exec api python -m app.db --db data/app.db --status
```

## 4. Hardening checklist for a real deployment

- [ ] Terminate TLS in front of the API (the stack speaks plain HTTP; the
      published port is loopback-only for exactly this reason).
- [ ] Keep `compose.prod.yml` for anything shared: it is the only file that
      refuses dev defaults and closes the broker.
- [ ] Install the scheduled job (`deploy/systemd/asa-ops.{service,timer}`) and
      set `ASA_OFFSITE_DIR`; a volume snapshot alone is not a backup, and the
      off-site copy is what makes the nightly anchor meaningful.
- [ ] Scrape `GET /metrics/prometheus` with a token that has `audit:read`, and
      alert at least on `asa_runs_failed_total` and the p95 NFR (300 ms).
- [ ] Watch the JSON logs (`level=WARNING`+) and the `429` rate — the
      in-process limiter is per replica, so add an edge limit when scaling out.
- [ ] Real `shell` execution (item [99]) needs unprivileged user namespaces in
      the API container. Measured on this host: with Docker's default seccomp
      profile `bwrap` is blocked and a `shell` step fails closed ("needs OS
      isolation"); with `security_opt: [seccomp=unconfined]` a command runs,
      isolated, and reports `bwrap-no-proc` because a fresh `/proc` mount is not
      permitted inside a container. Uncomment that line in `compose.yml` only on
      a host you control, or run command execution on a worker host with user
      namespaces (`docs/LIMITATIONS.md` §2).
- [ ] Set `ASA_WORKSPACE_ROOT` (compose sets `/code/data/workspaces`, inside the
      same named volume as SQLite) so a run's files survive a rebuild and can be
      inspected after the fact.

## 5. What was *not* verified

- **Another physical machine.** The run was a clean build on this host; the
  compose file, image and volumes are machine-independent, but no second
  machine was available. Item [88]'s "new machine" evidence is therefore
  *clean build + clean volumes on this host*, and should be repeated on the
  demo laptop before the defense.
- **PostgreSQL as the application store.** `db` is part of the topology and
  the driver is reserved, but the API persists to SQLite; the compose
  PostgreSQL is not exercised by the application yet (`docs/LIMITATIONS.md`).
- **Redis-backed runs inside compose.** The Redis Streams contract is tested
  against a real server in CI-gated tests, not through the compose stack
  during this run.
- **Multi-replica behaviour** (shared rate-limit state, SQLite
  single-writer) — see `docs/THREAT_MODEL.md` §5 and `docs/LIMITATIONS.md`.
- **Command execution in a *production* container.** It was probed in the
  development image on this host (both the refused and the `bwrap-no-proc` case,
  see §4); the production file does not opt into `seccomp=unconfined`, so there
  a `shell` step fails closed by design until the operator decides otherwise.
- **A host without user namespaces.** The plugin path is exercised there by the
  audit-hook tests; `shell` steps were not run on such a host because they are
  refused (that refusal *is* the tested behaviour).
