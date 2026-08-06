# Implementation status

Mirrors the phase/OK state of `SE_Proposal_v4.tmp` section 15 in version
control (the proposal file itself is gitignored by the `*tmp` rule).

Legend: `WIP` = in progress, `OK` = all items done **and** gate passed.

| Phase | Scope | Status | Gate evidence |
|---|---|---|---|
| 0 | Scope, repository and quality baseline | **OK** | install `requirements*.txt` clean; 215 tests + 194 subtests green; `GET /health` 200; ruff/mypy/coverage/bandit/pip-audit/docker build all pass locally |
| 1 | API, authentication and database foundation | **OK** | 26 integration tests: 401/403 on protected routes for unauthorized callers, 201/200 for OWNER; clean DB applies all 4 migrations exactly once via the checksum-tracked runner; full suite green (66.2% coverage) |
| 2 | Workflow DSL and validator | **OK** | 13 valid/invalid fixtures green; `POST /workflows/validate` → 200 valid / 422 `{stage,message,line,column}` invalid with a frozen stage vocabulary and 1-based positions; read-only (nothing persisted) + W4-novalidate probe |
| 3 | DAG executor, policy and approvals | **OK** | immutable version + pinned-run persistence; three-node DAG runs concurrently with timeout/retry/propagation + reverse compensation; `safety.policy_service` decides ALLOW/NEED_APPROVAL/DENY with score/reasons/quorum/policy-version; approval quorum+expiry state machine; adversarial policy suite; full suite + ruff + mypy green |
| 4 | Redis event bus and workers | **OK** | frozen 5-type contract enforced in code; producer/consumer-group + idempotency + ordering + DLQ contract tests (real Redis guarded to skip); shell/approval workers real, GUI/custom explicit stubs; step state persisted before ack; manual/file.arrived/schedule.tick triggers share one run path; duplicate trigger causes no duplicate side effect |
| 5 | Audit, metrics and replay | **OK** | append-only hash chain with canonical payloads; `GET /executions/{id}/audit` (paginated), `/timeline`, `/audit/verify` (first broken seq on tamper or deletion) and `/metrics/executions` (success/latency/p95/retries/approval time); real bus runs and approval decisions write their own chain keyed by execution id; replay re-executes the pinned version row (tampered pin refused) under a new correlation id and reports normalized per-node match/missing/unexpected; core coverage `src/engine|safety|storage|bus` = 85.3%; full suite + ruff + mypy green |
| 6 | Offline planner and optional LLM adapter | **OK** | frozen intents/slots + versioned `intents-v1` dataset (99 utterances); deterministic normalize→rules→slots pipeline measured at held-out accuracy 1.000, macro-F1 1.000, slot accuracy 1.000, 0.020 ms avg latency, 0.111 fallback rate (docs/PLANNER_EVAL.md); every plan goes through the shared loader/validator/policy (`compile_plan`, `policy_verdicts`); optional typed LLM adapter with timeout/retry/provider fallback and secret-free metadata; W1 acceptance test plans it, runs it over the bus and replays it |
| 7 | Memory, consent and reputation | **OK** | 122 Phase 7 tests (unit + integration + acceptance): consent grant/revoke gates every write (409 without it), plans are PII-scrubbed before persistence and never store the raw value, deterministic hashing embeddings rank with a stable tie-break, filters drop quarantined/high-risk/incompatible-OS items *before* scoring, reputation follows the 0.7/0.3 formula, and QUARANTINED→SANDBOX→PUBLIC is the only promotion path; `POST /memory/items/{id}/episodes` is the only way out of quarantine; erasure removes item + events but keeps consent history; `GET /memory/metrics` reports reuse hits and the planner calls they avoided (raw queries are hashed, never stored); gate acceptance test proves a proven safe plan is reused while a failed plan returns to quarantine and a PII plan is reused scrubbed; migrations 006–007 apply once via the checksum runner; full suite (811 tests, 651 subtests) + ruff + mypy green |
| 8 | Sandbox and client experience | **OK** | frozen `sandbox-v1` contract (relative-only paths, bounded timeout/output/seed budget, fixed result keys); two isolation layers exercised — `bwrap` namespaces on this host, audit hook as the tested fallback — and every escape vector (`../`, absolute path, symlink, delete outside, `/etc/passwd` read, `subprocess`, socket) denied with the outside tree byte-identical afterwards; wall-clock/memory/file-size limits kill the hog cases and `command` is refused without OS isolation; manifests validate before registration, unknown/duplicate/mis-declared plugins never run, and an escaping `custom` node becomes `step.failed` through the bus worker; four Tkinter screens (workflow, runs, approvals, audit) drive `GET /executions`, `GET /approvals`, decisions under `approval:decide` (409 on terminal), timeline and audit/verify, proven by a live-uvicorn walkthrough; `POST /workflows/dry-run` is inert (`persisted=False`, `dispatched=False`) and blocks an untrusted `rm -rf ../outside` plan; `POST /policy/explain` adds role-aware headline/why/next-step; 121 Phase 8 tests, full suite 933 passed + 2 skipped, ruff/format/mypy green |
| 9 | Security, usability and deployment hardening | **OK except [87]** | STRIDE model (5 trust boundaries, residual risks) with a test that keeps its citations honest; Bandit 0 medium/high, pip-audit clean on all three requirement sets, secret scan clean (269 files at the time; 278 at the RC) and a CI step; `compose.prod.yml` refuses to start without secrets, keeps db/redis unpublished and runs non-root; body cap + per-client rate limits (login refunded on success, 429 with Retry-After); structured JSON logs with key + rule redaction and a peer-address digest; measured p95 33.9 ms vs the 300 ms NFR (worst endpoint), shell worker ~129k events/s at the time (record-only then; item [99] replaced it with real execution), sandbox run 38 ms; clean compose build verified end to end and the run exposed/fixed state loss on redeploy (SQLite on a named volume); backup/restore with manifest + audit-chain verification and a 4-migration upgrade test; coverage core 86.1% / global 80.75% in that phase's run (ratchet raised to 78); **[87] SUS kit ready, five sessions pending** |
| 10 | Final SE package and defense rehearsal | **OK except [87] and [95] (human tasks)** | Reviewer-facing package frozen as tag `v1.0.0-rc1` (`docs/RELEASE_NOTES.md`): architecture/sequence/state diagrams render (`scripts/validate_diagrams.py`), requirements-to-test traceability is machine-checked (`scripts/traceability_report.py --check`; 25 requirements, 49 test files, 662 collected tests), every report is regenerated by `scripts/export_reports.sh` (13 steps, 0 failed: 1083 passed + 2 skipped, coverage 81.35% global / 86.55% core, worst p95 31.5–36.4 ms across runs vs the 300 ms target, Bandit 0 at medium+, pip-audit clean, secret scan clean), limitations (`docs/LIMITATIONS.md`), demo script (`docs/DEMO_SCRIPT.md`), rehearsal 13/13 PASS from a clean directory, defense Q&A (`docs/DEFENSE_QA.md`), AI usage log + review checklists + prompt archive (`docs/AI_USAGE_LOG.md`, `docs/REVIEW_CHECKLISTS.md`, `docs/prompts/`), plus the remaining §14 deliverables (ADRs `docs/ADR.md`, risk register `docs/RISK_REGISTER.md`); **[95] recording and [87] SUS sessions are human steps and are labelled PENDING, not done** |

## Phase 10 item log (closed 2026-10-18, [95] recording pending)

| # | Item | Status | Commit |
|---|---|---|---|
| 90 | Complete UML and architecture diagrams | OK | `641d769` docs(arch): architecture, sequence and state diagrams |
| 91 | Complete requirements-to-test traceability | OK | `bcd2ade` docs(trace): machine-checked requirements-to-test traceability |
| 92 | Freeze release candidate and tag it | OK | tag `v1.0.0-rc1` on the freeze commit; `docs/RELEASE_NOTES.md` |
| 93 | Export test, coverage, security and performance reports | OK | `scripts/export_reports.sh`, `scripts/bandit_summary.py`, `docs/reports/*` (13 steps, 0 failed) |
| 94 | Write limitations and deferred-feature section | OK | `630ce61` docs: limitations and deferred features |
| 95 | Prepare three-minute backup video | **SCRIPT READY, RECORDING PENDING (human)** | `docs/DEMO_SCRIPT.md` (timings, commands, fallback table) + rehearsal transcript |
| 96 | Rehearse W1, W2 and optional W3 from a clean environment | OK | `scripts/rehearse_demo.sh` → `docs/reports/rehearsal.txt` (13/13 PASS, live API smoke included) |
| 97 | Prepare answers for safety, AI ownership, reuse, failure recovery and scalability | OK | `docs/DEFENSE_QA.md` (19 answers + cross-questions, each with evidence and stated limits) |
| 98 | Archive prompt logs and human review checklists | OK | `docs/AI_USAGE_LOG.md`, `docs/REVIEW_CHECKLISTS.md`, `docs/prompts/` (phase 10 session verbatim; earlier phases marked *reconstructed*) |

### Phase 10 gate — a reviewer can connect every major requirement to code, test and demo evidence

**OK, with the two human rows above still PENDING.** The chain is: requirement
`FR-nn`/`NFR-x` → module → test (`docs/reports/traceability.md`, machine-checked)
→ demo step (`docs/DEMO_SCRIPT.md`) → measured number (`docs/reports/*`).
Additional §14 deliverables closed in this phase: ADRs (`docs/ADR.md`, 10
records with rejected alternatives), risk register (`docs/RISK_REGISTER.md`, 9
proposal risks + 5 found while building, each with residual exposure), and the
release notes (`docs/RELEASE_NOTES.md`).

## Post-RC hardening (items 99+, after tag `v1.0.0-rc1`)

Work on the limitations list that `docs/LIMITATIONS.md` records, each item
closed with its own commit, tests and doc update — and with the limitation row
*removed* only because the tests that prove the new behaviour exist.

| # | Item | Status | Evidence |
|---|---|---|---|
| 99 | Execute `shell` nodes for real, inside the sandbox | OK | `src/sandbox/commands.py` (command → `kind="command"` request; refuses an empty or unisolated command), `sandbox.runner.run(..., workspace_dir=...)` (a caller-owned workspace is the only writable mount, with scratch space outside it), `bus/workers/shell.py` (gate → validated workspace → sandboxed run → bounded deterministic output + file diff), `bus/workers/dispatcher.resolve_run_workspace` + `PolicyContext(workspace_root=...)` (path escapes DENIED before dispatch; `ASA_WORKSPACE_ROOT` or a throwaway run workspace), `tests/integration/test_shell_execution.py` (real files created, deterministic output, timeout kills the step, escape denied pre-dispatch with the reason on the audit chain, fail-closed without OS isolation), `tests/unit/test_sandbox_commands.py` (argv/clamps/refusal/real run), `scripts/rehearse_demo.sh` step added; `docs/LIMITATIONS.md` row removed, `docs/ADR.md` ADR-011 records the decision and its price |
| 100 | Prometheus metrics + a CI performance regression gate | OK | `src/app/metrics_export.py` renders text exposition v0.0.4 from the same aggregates as `/metrics/executions`; `GET /metrics/prometheus` (auth `audit:read`) adds memory item counts, reuse rate and `asa_build_info`; `scripts/benchmark.py` gained `--api-only`/`--max-p95` and `.github/workflows/ci.yml` runs it on every push, failing at the 300 ms NFR budget; `tests/unit/test_metrics_export.py` pins every line and escaping, `tests/integration/test_audit_api.py` covers auth + content-type; `docs/LIMITATIONS.md` row replaced |
| 101 | Retention/expiry and scheduled, off-host backups | OK | `src/storage/retention.py` (quarantined evidence and unreadable timestamps are never purged, cutoff is exact, backup pruning deterministic), `scripts/retention.py` (dry-run/apply, one `memory.retention` audit record, consent history kept), `scripts/backup.py prune` (never removes the last snapshot) and `backup --copy-to` (re-hashes the copy), `scripts/ops_job.sh` (backup → verify → prune → retention → anchor, stops at first failure), `deploy/systemd/asa-ops.{service,timer}` (`Persistent=true`); `tests/unit/test_retention.py`, `tests/integration/test_ops_scripts.py`; `docs/DEPLOYMENT.md` §3/§4 |
| 102 | Chaos/recovery suite | OK | `tests/integration/test_chaos_recovery.py`: broker read outage (worker survives, `RunStats.backend_errors`), lost acknowledgement (side effect runs once in the pass, delivery stays pending, replay resolves it without duplicate rows), transient and permanent storage failure (retry vs DLQ), `Runner.redrive_dlq` recovery that keeps the parked record, and a restart that re-reads run/steps/audit chain from disk; `src/bus/consumer.py` hardened so `serve()` retries instead of dying when the broker is unreachable |
| 103 | Audit anchoring so a full chain rewrite is detectable | OK | `src/storage/anchor.py` (per-chain heads, one SHA-256 digest, optional HMAC), `scripts/anchor_audit.py anchor|verify` (exit 1 + named chains on mismatch), `AuditStore.execution_ids`; `tests/unit/test_audit_anchor.py` rewrites a whole chain consistently — `verify_chain` still passes, the anchor fails — plus added/removed chains and a tampered signature; wired into `scripts/ops_job.sh` and `docs/DEPLOYMENT.md` §3 |

## Phase 9 item log (closed 2026-10-18, [87] pending)

| # | Item | Status | Commit |
|---|---|---|---|
| 81 | Complete STRIDE threat model | OK | `305d26c` docs(security): STRIDE threat model tied to code and tests |
| 82 | Run Bandit, dependency audit and secret scan | OK | `bf727ba` security: secret scan, audited Bandit baseline and a written report, `afd58a4` build(deps): pin the audited dependency versions |
| 83 | Remove development default secrets from production configuration | OK | `503a625` build: production compose that refuses development default secrets |
| 84 | Add rate limits and request validation where needed | OK | `ef07d0f` feat(app): body cap and per-client rate limits at the edge |
| 85 | Add structured log redaction | OK | `0f3fd8f` feat(app): structured JSON logs that cannot carry secrets |
| 86 | Benchmark API p95 and worker throughput | OK | `9edb947` perf: measured API p95 and worker throughput |
| 87 | Run SUS study with five users | **PENDING (human sessions)** | `c561f1d` docs(ux): SUS protocol, instrument and a tested scorer — results table still empty |
| 88 | Verify clean compose deployment from a new machine | OK | `94da58a` ops: verify the compose deployment and stop losing state on redeploy |
| 89 | Test backup/restore and migration upgrade path | OK | `2419eef` ops: trustworthy backup/restore and a tested migration upgrade path |

## Phase 8 item log (closed 2026-10-18)

| # | Item | Status | Commit |
|---|---|---|---|
| 73 | Define sandbox request/response contract | OK | `b347832` feat(sandbox): frozen contract and enforced isolation limits |
| 74 | Enforce timeout, no network, restricted filesystem and resource limits | OK | `b347832` feat(sandbox): frozen contract and enforced isolation limits |
| 75 | Add plugin manifest validation | OK | `64a1b03` feat(sandbox): validate plugin manifests and run custom nodes sandboxed |
| 76 | Run custom nodes sandbox-first | OK | `64a1b03` feat(sandbox): validate plugin manifests and run custom nodes sandboxed |
| 77 | Add Tkinter screens for workflow, run, approval and audit views | OK | `bdf9b55` feat(client): workbench screens for workflow, runs, approvals and audit |
| 78 | Add human-readable policy explanations | OK | `861a980` feat(safety): readable policy explanations and a safe dry-run |
| 79 | Add a safe dry-run mode | OK | `861a980` feat(safety): readable policy explanations and a safe dry-run |
| 80 | Add sandbox contract and UI walkthrough tests | OK | `b6217e1` test(client): walk the workbench screens against a live API |

## Phase 7 item log (closed 2026-10-17)

| # | Item | Status | Commit |
|---|---|---|---|
| 64 | Implement consent records and revocation | OK | `45c2e65` feat(memory): consent records with revocation and memory schema |
| 65 | Implement PII scrub before persistence | OK | `3262a98` feat(memory): PII and secret scrubbing before persistence |
| 66 | Add deterministic local embeddings for tests | OK | `6d450ae` feat(memory): deterministic local hashing embeddings |
| 67 | Add similarity retrieval and top-k ranking | OK | `df2f8ab` feat(memory): similarity retrieval with deterministic top-k ranking |
| 68 | Filter by risk, OS compatibility and quarantine state | OK | `d89eb06` feat(memory): filter reuse by risk, OS and quarantine state |
| 69 | Implement reputation formula and update rules | OK | `474f670` feat(memory): explainable reputation formula and update rules |
| 70 | Implement QUARANTINED, SANDBOX and PUBLIC states | OK | `14c0298` feat(memory): consent-gated store with quarantine, promotion and erasure |
| 71 | Add erasure endpoint and erasure integration test | OK | `01a9708` feat(app): memory consent, store, search and erasure endpoints |
| 72 | Add reuse-hit and no-generation-cost metrics | OK | `a6d33e2` feat(memory): reuse-hit, no-generation-cost metrics and outcome endpoint |

## Phase 6 item log (closed 2026-10-17)

| # | Item | Status | Commit |
|---|---|---|---|
| 55 | Define supported intents and slots | OK | `d4c1874` feat(planner): freeze intents, slots and the canonical plan schema |
| 56 | Create and version a small labeled dataset | OK | `4f6c508` feat(planner): versioned labeled dataset for supported intents |
| 57 | Implement normalization and high-precision rules | OK | `b54bf5c` feat(planner): deterministic normalization and high-precision rules |
| 58 | Implement slot extraction and canonical task schema | OK | `97cc372` feat(planner): slot extraction, canonical steps and the validated plan path |
| 59 | Add confidence threshold and clarification response | OK | `f7bfa50` feat(planner): confidence threshold and clarification responses |
| 60 | Add typed LLM adapter behind an interface | OK | `207ee5d` feat(planner): typed LLM adapter behind a model-client interface |
| 61 | Add timeout, retry, provider failure and invalid-response handling | OK | `3dea7d5` feat(planner): timeouts, retries and provider fallback for the LLM adapter |
| 62 | Record provider metadata without storing secrets | OK | `3a23786` feat(planner): planner service records provider metadata without secrets |
| 63 | Measure precision, recall, F1, latency and fallback rate | OK | `e81699a` feat(planner): measure precision, recall, F1, latency and fallback rate |

## Phase 5 item log (closed 2026-10-17)

| # | Item | Status | Commit |
|---|---|---|---|
| 46 | Add append-only audit repository | OK | `cdf611d` feat(storage): append-only audit repository with pagination |
| 47 | Add canonical payload hashing and hash-chain verification | OK | `cbe8e49` feat(storage): canonical payload hashing and chain verification |
| 48 | Record policy, approval, dispatch, retry, result and replay events | OK | `6d481d4` feat(storage): record policy/approval/dispatch/retry/result/replay, wired into the run path and approvals by `8aba175` |
| 49 | Add audit query endpoint with pagination | OK | `9c5a8e1` feat(app): audit query and execution timeline endpoints |
| 50 | Add execution timeline endpoint | OK | `9c5a8e1` feat(app): audit query and execution timeline endpoints |
| 51 | Add metrics for success, latency, retries and approval time | OK | `d69bda6` feat(storage): execution metrics for success, latency, retries and approvals |
| 52 | Implement replay with source-version pinning | OK | `cef01a0` feat(engine): replay runs from the pinned workflow version |
| 53 | Implement normalized output comparison | OK | `4652150` feat(engine): normalized output comparison for replay |
| 54 | Add tamper and replay tests | OK | `5b81543` test(phase5): audit verification endpoint plus tamper and replay evidence |

## Phase 0 item log (closed 2026-10-15)

| # | Item | Status | Commit |
|---|---|---|---|
| 1 | Approve proposal and freeze MVP scope | OK | `db260cb` docs(phase0): approve proposal v4 and freeze MVP scope |
| 2 | Create GitHub issues from this TODO and assign owners | OK | `47244b5` docs(phase0): add numbered issue backlog with owners |
| 3 | Record external source/license inventory | OK | `abf3ccb` docs(phase0): record external source and license inventory |
| 4 | Define coding, branching, review and commit conventions | OK | `15cb0a5` docs(phase0): define coding, branching, review and commit conventions |
| 5 | Add `tests/` structure for unit, integration and acceptance tests | OK | `66e3a16` test(phase0): add tests/ structure with unit, integration, acceptance suites |
| 6 | Add formatter, linter, type checker and coverage configuration | OK | `7ffed59` chore(phase0): add ruff/mypy/pytest/coverage config and make the tree green |
| 7 | Add CI workflow for tests and Docker build | OK | `3617a66` ci(phase0): add CI workflow for quality gates and Docker build |
| 8 | Update README with clean setup and architecture | OK | `f971668` docs(phase0): rewrite README with clean setup and architecture |
| 9 | Create requirements traceability matrix | OK | `42b29b8` docs(phase0): add requirements traceability matrix |

## Phase 1 item log (closed 2026-10-15)

| # | Item | Status | Commit |
|---|---|---|---|
| 10 | Finalize settings and environment validation | OK | `aa2d56e` feat(app): validate settings and environment at startup |
| 11 | Implement password hashing and short-lived JWT | OK | `961f56c` feat(app): real HS256 JWT and salted PBKDF2 password hashing |
| 12 | Implement OWNER, OPERATOR, APPROVER and VIEWER roles | OK | `d9b101e` feat(app): RBAC roles with frozen permission matrix |
| 13 | Add authorization dependencies to protected routes | OK | `1e001f5` feat(app): authorization dependencies on protected routes |
| 14 | Add teams, members and workspaces | OK | `e642cb0` feat(app): teams, members and workspaces with scoped roles |
| 15 | Add reproducible migration runner | OK | `0103b9d` feat(app): reproducible migration runner with drift detection |
| 16 | Add repository interfaces and transaction boundaries | OK | `0b6a32c` feat(app): repository interfaces and transaction boundaries |
| 17 | Add migration and RBAC integration tests | OK | `f971a2e` test(phase1): migration and RBAC integration tests |

## Phase 2 item log (closed 2026-10-16)

| # | Item | Status | Commit |
|---|---|---|---|
| 18 | Freeze the five node types and three trigger types | OK | `1cb58de` feat(engine-dsl): freeze five node types and three trigger types |
| 19 | Implement typed YAML loader with source locations | OK | `de51f28` feat(engine-dsl): typed loader reports YAML/JSON syntax locations |
| 20 | Implement schema and parameter validation | OK | `dc9f119` feat(engine-validator): schema and parameter contracts |
| 21 | Implement duplicate ID, edge and placeholder checks | OK | `0125fa0` feat(engine-validator): duplicate id, edge and placeholder checks |
| 22 | Implement Kahn cycle detection and deterministic topological order | OK | `bb62e68` feat(engine-validator): deterministic Kahn topological order |
| 23 | Implement static dangerous-node approval validation | OK | `8ac093f` feat(engine-validator): static dangerous-node approval validation |
| 24 | Add at least ten valid/invalid fixtures | OK | `dd87511` test(engine-validator): grow fixture set to thirteen valid/invalid YAMLs |
| 25 | Expose `POST /workflows/validate` | OK | `85ae6b2` feat(app): expose POST /workflows/validate |
| 26 | Add precise validation error responses | OK | `5be1075` feat(app): precise validation error responses with frozen stages |

## Phase 3 item log (closed 2026-10-16)

| # | Item | Status | Commit |
|---|---|---|---|
| 27 | Add workflow/version persistence | OK | `4a8757c` feat(app): workflow and immutable version persistence |
| 28 | Add execution and execution-step persistence | OK | `2ea4a7b` test(phase3): execution and execution-step persistence |
| 29 | Implement READY/RUNNING/SUCCEEDED/FAILED/BLOCKED states | OK | `1d8619f` test(phase3): node lifecycle state machine |
| 30 | Implement concurrency for independent nodes | OK | `06cf0ce` test(phase3): independent nodes run concurrently |
| 31 | Implement timeout, retry and failure propagation | OK | `c9243c4` test(phase3): timeout, retry and failure propagation |
| 32 | Implement compensation contract | OK | `185e3ab` test(phase3): compensation contract |
| 33 | Extract policy decisions into a dedicated service | OK | `9746450` feat(safety): dedicated policy service with one decision per node |
| 34 | Add workspace, role, path and command checks | OK | `d335b61` feat(safety): workspace, role and path boundary checks |
| 35 | Implement approval quorum and expiration | OK | `a53361c` feat(app): approval quorum and expiration state machine |
| 36 | Add policy boundary and adversarial tests | OK | `c8af776` test(phase3): policy boundary and adversarial cases |

## Phase 4 item log (closed 2026-10-16)

| # | Item | Status | Commit |
|---|---|---|---|
| 37 | Freeze event JSON schemas | OK | `3a4ec2d` test(phase4): freeze the bus event JSON schemas |
| 38 | Implement Redis Streams producer and consumer group | OK | `97aabba` test(phase4): broker producer and consumer group contract |
| 39 | Add correlation and idempotency key handling | OK | `dbd5cd1` test(phase4): correlation and idempotency key handling |
| 40 | Add shell and approval workers | OK | `d1bfca0` feat(bus): shell and approval workers with defense-in-depth gate |
| 41 | Add GUI/custom contract workers as explicit non-production stubs | OK | `e990107` test(phase4): GUI/custom contract workers are explicit stubs |
| 42 | Implement retry backoff and dead-letter stream | OK | `7f1028a` test(phase4): retry backoff and dead-letter stream |
| 43 | Persist step state before acknowledging events | OK | `3afaaef` feat(bus): persist step state before acknowledging events |
| 44 | Implement manual, file-arrival and schedule triggers | OK | `91c0ebc` feat(bus): manual, file.arrived and schedule.tick triggers |
| 45 | Add duplicate-delivery, ordering and DLQ tests | OK | `7906156` test(phase4): duplicate-delivery, ordering and DLQ integration tests |
