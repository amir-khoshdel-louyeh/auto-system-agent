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

### Backup (Phase 9 item [89])

```bash
python scripts/backup.py backup --db data/app.db --out backups/ --name nightly
python scripts/backup.py verify backups/nightly.db
```

The snapshot is taken with SQLite's online-backup API (consistent while the
API is writing) and accompanied by `nightly.db.manifest.json`: SHA-256, size,
every applied migration with its checksum, table row counts, and the result
of verifying **every** audit chain in the copy. `verify` re-checks all of it.

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
- [ ] Run `python scripts/backup.py backup` on a schedule and copy the
      snapshots off the host; a volume snapshot alone is not a backup.
- [ ] Watch the JSON logs (`level=WARNING`+) and the `429` rate — the
      in-process limiter is per replica, so add an edge limit when scaling out.
- [ ] Rotate `SECRET_KEY` and the admin password through the environment, not
      through an image rebuild.

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
