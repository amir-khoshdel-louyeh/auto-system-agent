# Architecture decision records (deliverable §14.6)

**Scope:** the decisions a reviewer would otherwise have to reverse-engineer
from the code. Ten records, each with the alternative we rejected and the price
we still pay for the choice. Status is `Accepted` unless stated; the date is the
phase that froze it. Evidence points at code, tests or a measured report — a
decision with no evidence is an intention, not a decision.

Format: **Context → Decision → Alternatives rejected → Consequences (+/−) →
Evidence.**

---

## ADR-001 — MVP-first scope with explicit stubs rather than unfinished features
**Phase 0, 2026-10-15.**

- **Context.** A two-student project with a stretch list (VLM GUI control,
  marketplace, Kubernetes) and a fixed deadline.
- **Decision.** Freeze the MVP; anything outside it is either *implemented*,
  *refused*, or an **explicit stub that says so in its own output** (GUI and
  cross-platform automation). No silent no-ops, ever.
- **Alternatives rejected.** (a) Attempt GUI automation and risk an unreliable
  demo; (b) drop the node types from the DSL, losing the target architecture.
- **Consequences.** + Every claim in the demo is backed by a working path; + the
  DSL stays complete for a later adapter. − Two node types are visible but not
  productive, which must be stated out loud (`docs/LIMITATIONS.md` §1).
- **Evidence.** `src/bus/workers/stubs.py`, `tests/unit/test_bus_stubs.py`,
  `docs/PROPOSAL_APPROVAL.md`.

## ADR-002 — A deterministic policy service is the only authority on risk
**Phase 3, 2026-10-16.**

- **Context.** An LLM plans; the plan may be wrong, hostile or both.
- **Decision.** `safety/policy_service.py` returns exactly `ALLOW` /
  `NEED_APPROVAL` / `DENY` with score, reasons, quorum and policy version. The
  planner, the API and the LLM adapter have no authority; the gate fails closed
  on anything it cannot read, and the worker re-decides as defence in depth.
- **Alternatives rejected.** Model-based risk scoring (non-deterministic, no
  audit trail), per-call-site ad-hoc checks (drift), allow-by-default with
  logging (unsafe).
- **Consequences.** + Reproducible verdicts on the audit chain; + one place to
  test adversarial cases. − Risk logic must be maintained by hand and a new
  node type needs a rule.
- **Evidence.** `src/safety/policy_service.py`, `src/engine/executor/gate.py`
  (flatten + fail-closed), `tests/unit/test_policy_adversarial.py`,
  `tests/unit/test_engine_gate.py`.

## ADR-003 — Freeze the workflow DSL at 5 node types and 3 trigger types
**Phase 2, 2026-10-16.**

- **Context.** A DSL that grows during implementation cannot be validated,
  tested or taught to a planner.
- **Decision.** `shell`, `gui_click`, `gui_type`, `approval`, `custom`; triggers
  `manual`, `cron`, `event: file.arrived`. New types are an explicit scope
  guard; a compiler and a 13-fixture validator gate every document.
- **Alternatives rejected.** A generic "any command" node (unvalidatable);
  free-form step dictionaries (no line/column diagnostics).
- **Consequences.** + Precise `stage:line:column` errors and a planner that
  targets a closed vocabulary. − Real-world coverage needs a plugin story
  (hence `custom` + the manifest).
- **Evidence.** `src/engine/dsl/grammar.py`, `src/engine/validator/validator.py`,
  `tests/unit/test_engine_phase1.py`, `src/sandbox/plugins.py`.

## ADR-004 — Redis Streams, consumer groups and a DLQ for the bus
**Phase 4, 2026-10-16.**

- **Context.** Work must survive process death and duplicate delivery.
- **Decision.** One frozen event contract (`src/bus/events.py`), consumer groups
  per step type, correlation id + idempotency key on every event, bounded
  retries with exponential backoff and a dead-letter stream; the step's durable
  state is written **before** the verdict and the ack.
- **Alternatives rejected.** In-process queues (no recovery), a database as a
  queue (locking), Kafka (operational weight for two students), at-most-once
  delivery (lost work).
- **Consequences.** + At-least-once with no duplicate side effect, observable
  failures. − Two processes to run; ordering is per stream, not global.
- **Evidence.** `src/bus/consumer.py`, `src/bus/workers/core.py` (item [43]),
  `tests/unit/test_bus_idempotency.py`, `tests/integration/test_bus_integration.py`.

## ADR-005 — SQLite for the application store, prepared for PostgreSQL
**Phase 1, 2026-10-16.**

- **Context.** The build must be hermetic and fast on a laptop; the target
  architecture says PostgreSQL.
- **Decision.** SQLite with a checksum-tracked migration runner and a repository
  layer that speaks DB-API; `asyncpg` pinned, the compose stack already runs
  PostgreSQL as topology. The trade-off is documented, not hidden.
- **Alternatives rejected.** PostgreSQL from day one (slower tests, harder clean
  setup), an ORM-heavy abstraction (hides the SQL we actually want to review).
- **Consequences.** + `pytest` runs with no services; + drift detection on
  migrations. − Single writer, no horizontal API scaling
  (`docs/LIMITATIONS.md` §2).
- **Evidence.** `src/app/db.py` (checksums, drift), `src/app/repositories.py`,
  `tests/integration/test_app_migration.py`, `compose.yml`.

## ADR-006 — Append-only hash-chain audit, not a log table
**Phase 5, 2026-10-17.**

- **Context.** "Trust us, we logged it" is not evidence a reviewer can test.
- **Decision.** Append-only repository; each record's hash covers the canonical
  payload plus the previous hash; verification names the first broken sequence
  number after a tamper, a deletion or a relink; runs and approval decisions
  each write their own chain.
- **Alternatives rejected.** Plain append-only table (no tamper evidence),
  external blockchain/anchor service (out of MVP scope, named as residual risk).
- **Consequences.** + Tamper evidence that is testable and explainable. − No
  global ordering across executions, and a host-level attacker who rewrites the
  whole chain is out of scope.
- **Evidence.** `src/storage/audit.py`, `tests/unit/test_audit_chain.py`,
  `tests/integration/test_audit_api.py`.

## ADR-007 — Sandboxing: frozen contract, namespaces, audit-hook fallback, refuse when neither
**Phase 8, 2026-10-17.**

- **Context.** Untrusted plugin code must run without becoming a host backdoor;
  `bwrap` is not available everywhere (it was absent in one test environment).
- **Decision.** A frozen request/result contract (relative paths only, bounded
  timeout/output/seed); `bwrap` user namespaces where available, a Python audit
  hook as the tested fallback; if neither can isolate the request, a `command`
  node is **refused** rather than executed unguarded.
- **Alternatives rejected.** Docker-in-Docker (heavy, CI-hostile), `subprocess`
  with a command denylist (trivially defeated), skipping sandboxing for "our own"
  plugins (no such thing as trusted third-party code).
- **Consequences.** + Escape attempts are denied *in tests*, on this host. −
  Namespace isolation is not a hardened container runtime, and the honest
  threat boundary is kernel escape.
- **Evidence.** `src/sandbox/{contract,runner,harness,plugins}.py`,
  `tests/acceptance/test_sandbox_isolation.py`, `docs/THREAT_MODEL.md`.

## ADR-008 — Stored plans are consent-gated, scrubbed and quarantine-first
**Phase 7, 2026-10-17.**

- **Context.** Reusing a previously "successful" plan is only safe if the plan
  is safe *now*, and PII must not become a knowledge base.
- **Decision.** Consent required for every write and revocable; content scrubbed
  before persistence and refused if a rule still matches; reuse filtered by risk,
  OS compatibility and quarantine state *before* scoring; promotion is
  `QUARANTINED → SANDBOXED → PUBLIC` only; erasure removes items and events but
  keeps the consent history.
- **Alternatives rejected.** Optimistic reuse of anything that once passed (a
  single success proves little), storing raw text and scrubbing at read time
  (the leak is the storage), embeddings requiring a model download (breaks the
  hermetic test run).
- **Consequences.** + Reuse is measured and explainable. − Rule-based scrubbing
  can over-redact; hashing embeddings are not semantic.
- **Evidence.** `src/memory/{scrub,store,states,reputation}.py`,
  `tests/acceptance/test_memory_reuse_gate.py`,
  `tests/integration/test_memory_api.py`.

## ADR-009 — Deterministic planner first, typed LLM adapter second
**Phase 6, 2026-10-17.**

- **Context.** The project must demo without a provider, yet show a credible AI
  path.
- **Decision.** Normalize → high-precision rules → slot extraction over a frozen
  intent/slot vocabulary and a versioned dataset; the LLM adapter is optional,
  typed, schema-validated, timeout/retry/fallback aware and records secret-free
  metadata. Every plan — from either path — goes through the same validator and
  policy service.
- **Alternatives rejected.** LLM-first planning (non-deterministic demo, no
  provider ⇒ no product), an untyped "text in, JSON out" adapter (unvalidatable).
- **Consequences.** + Measured, reproducible planning (accuracy 1.000 on the
  frozen dataset, 0.111 fallback) with a first-class clarification response. −
  Nine intents only.
- **Evidence.** `docs/PLANNER_EVAL.md`, `src/orchestration/offline_planner.py`,
  `src/orchestration/llm_adapter.py`, `tests/unit/test_llm_adapter.py`.

## ADR-010 — Stdlib-first client and tooling
**Phase 0/8, 2026-10-15.**

- **Context.** A demo must run on a lecture-hall machine with nothing installed.
- **Decision.** Tkinter client and `urllib` API client from the standard library;
  the API adds FastAPI/uvicorn/Redis; dev tooling is pinned to an audited set.
- **Alternatives rejected.** An npm/React front end (build toolchain in the
  critical path of a demo), a heavy HTTP client (dependency for no benefit).
- **Consequences.** + `python main.py --gui` works on a bare install; + small
  dependency surface to audit. − Widget tree is not automated in CI, and Tkinter
  theming/accessibility are limited.
- **Evidence.** `src/ui/`, `requirements*.txt`, `docs/reports/pip-audit.txt`,
  `tests/acceptance/test_workbench_walkthrough.py`.

## ADR-011 — `shell` commands execute inside the sandbox, in a per-run workspace
**Post-RC hardening, item [99], 2026-10-18.**

- **Context.** The shell worker was record-only: the gate decided, the step
  recorded the command, and nothing ran. That is safe but it makes the platform
  a simulator, and it hides the one risk the sandbox exists for — real commands.
- **Decision.** A `shell` node executes `/bin/sh -c <run>` **inside** the Phase 8
  sandbox, in a workspace the dispatcher creates for the run
  (`<ASA_WORKSPACE_ROOT>/<run_id>`, or a throwaway directory when no root is
  configured). The gate receives that workspace as its `PolicyContext`, so a
  command that names a path outside it is DENIED *before dispatch*; the worker
  re-gates, validates any workspace handed to it against the allowed roots, and
  a step fails closed when the host cannot isolate commands at all. The step
  output is deterministic (no durations) because replay compares it.
- **Alternatives rejected.** (a) Running the command in the worker process with
  the command guard — one parser bypass away from a host shell; (b) keeping the
  record-only worker and calling it a limitation — the gap was the largest one
  in the product, not a design choice; (c) executing on the host and "auditing
  harder" — audit records damage, it does not prevent it.
- **Consequences.** + The W1/W2 scenarios now do what they say; + a command's
  effects are confined to a directory the operator can inspect. − Real execution
  needs `bwrap` for `shell` nodes on the target host (the step fails without
  it); − the shell is still a shell inside the sandbox, so the policy gate and
  the workspace boundary remain the only guard rails, and the sandbox is still
  namespace isolation rather than a hardened runtime (`docs/LIMITATIONS.md` §2).
- **Evidence.** `src/sandbox/commands.py`, `src/bus/workers/shell.py`,
  `src/bus/workers/dispatcher.py` (`resolve_run_workspace`),
  `tests/integration/test_shell_execution.py`,
  `tests/unit/test_sandbox_commands.py`, `tests/unit/test_engine_gate.py`
  (nested-parameter defence in depth).

## ADR-012 — Anchor the audit chain heads, not a blockchain
**Post-RC hardening, item [103], 2026-10-18.**

- **Context.** `verify_chain` detects an edited row, a deleted row and a relink,
  because the next row's `prev_hash` no longer matches. An attacker who rewrites
  **every** row of a chain, recomputing all hashes, produces a chain that still
  verifies — the head hash simply changed. `docs/LIMITATIONS.md` §2 recorded
  that residual risk and left it open.
- **Decision.** A nightly job writes an anchor document containing one
  `(execution_id, events, head_hash)` row per chain, one SHA-256 over all of
  them, and an HMAC-SHA256 over the document when `ASA_ANCHOR_KEY` is set. The
  operator copies `data/anchors/` off the host. `scripts/anchor_audit.py verify`
  recomputes the digest and names added, missing and rewritten chains.
- **Alternatives rejected.** (a) A blockchain or external timestamping service
  — a dependency and an operational story for a demonstrator; (b) a single
  global chain — merges concurrency, so one stuck writer stalls every run;
  (c) doing nothing because "the DB file is on a trusted host" — that is the
  assumption the anchor exists to test.
- **Consequences.** + A full-chain rewrite is detectable **if** the anchor copy
  is somewhere the attacker does not control; + no payloads or paths leave the
  host, only hashes. − The evidence is only as good as the off-host copy, and
  the job does not publish the digest anywhere on its own.
- **Evidence.** `src/storage/anchor.py`, `scripts/anchor_audit.py`,
  `tests/unit/test_audit_anchor.py` (a consistently rewritten chain passes
  `verify_chain` and fails the anchor), `tests/integration/test_ops_scripts.py`,
  `deploy/systemd/asa-ops.*`.

## ADR-013 — Retention and scheduled operations as scripts, not in-app timers
**Post-RC hardening, items [100]-[102], 2026-10-18.**

- **Context.** Memory items lived forever, backups were a command an operator
  had to remember, a broker outage could kill `serve()`, and the p95 NFR was
  measured but not enforced. Each of those is operational, not request-path.
- **Decision.** Keep the request path free of timers: `scripts/retention.py`
  applies `storage.retention.RetentionPolicy` (quarantined evidence and
  unreadable timestamps are never purged, the purge is audited on the chain),
  `scripts/ops_job.sh` chains backup → verify → prune → retention → anchor and
  stops at the first failure, systemd units schedule it, and CI runs the same
  benchmark with `--api-only` as a p95 gate. `Runner.serve()` treats an
  unreachable broker as a counted condition with backoff instead of a crash;
  `redrive_dlq` finishes parked work after recovery without erasing the parked
  record.
- **Alternatives rejected.** (a) In-process `asyncio` timers in the API — N
  replicas would run N purges and N backups; (b) deleting from the DLQ after a
  redrive — erases the evidence of the outage; (c) a relative throughput gate
  in CI — machine-dependent numbers make a flaky build, so the absolute NFR
  budget is gated and throughput stays a measured report.
- **Consequences.** + One operator command and one unit file cover the nightly
  job; failures are loud and ordered. − Scheduling is only as reliable as the
  host's systemd, and a failed timer is visible in journald but not alerted
  (`docs/LIMITATIONS.md` §3).
- **Evidence.** `src/storage/retention.py`, `src/bus/consumer.py`,
  `scripts/{retention,ops_job.sh,backup,benchmark}.py`,
  `tests/{unit/test_retention.py,integration/test_chaos_recovery.py,integration/test_ops_scripts.py}`,
  `.github/workflows/ci.yml`.

---

## Decisions we deliberately did **not** take (and why)

| Proposal | Why not | Where the cost is recorded |
|---|---|---|
| Blockchain / third-party timestamping | a local signed anchor covers the rewrite case without a dependency; publishing the digest externally remains the operator's step (ADR-012) | `docs/LIMITATIONS.md` §2 |
| Kubernetes / autoscaling | one-host deployment is what is actually verified | §2, §7 of the same file |
| OAuth/SSO | local users + short-lived JWT cover the RBAC demonstration | §1 |
| Per-node dynamic plugin loading | manifests validate before registration; a marketplace is out of scope | §1 |
