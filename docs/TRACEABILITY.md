# Requirements traceability matrix

**Item:** Phase 0 — [9] Create requirements traceability matrix
**Sources:** `SE_Proposal_v4.tmp` §3 (objectives), §6 (FR-01…FR-16), §7 (NFRs)
**Status:** initial matrix — completed and frozen in Phase 10 (item [91]).
**Legend:** `OK` = covered by an existing test today · `planned(item N)` = the
test arrives with the numbered TODO item; a requirement is never closed
without its evidence.

## 1. Objectives → evidence

| ID | Objective | Primary evidence | State |
|---|---|---|---|
| O1 | Execute OS tasks through safe, typed workers | `tests/unit/test_execution_safety.py`, `tests/acceptance/test_e2e_workflows.py`, `tests/unit/test_command_guard.py` | OK |
| O2 | Versioned workflow DAGs (YAML DSL, validator, compiler, pin) | `tests/unit/test_engine_phase1.py` (13 fixtures, Kahn, placeholders), `tests/integration/test_workflows_validate.py` (endpoint) | OK (DSL/validator/fixtures); version pin → item 27 |
| O3 | Deterministic policy prevents unsafe execution | `tests/unit/test_policy_service.py`, `test_policy_context.py`, `test_policy_adversarial.py`, `tests/unit/test_command_guard.py` | OK |
| O4 | Reliable event-driven execution (streams, retry, DLQ, idempotency, replay) | `tests/unit/test_bus_*.py`, `tests/integration/test_bus_integration.py`, `tests/acceptance/test_replay.py`, `tests/integration/test_replay.py` | OK |
| O5 | Team governance: JWT/RBAC, approvals, hash-chain audit | `tests/integration/test_app_auth.py` (login), `tests/unit/test_audit_chain.py` (hash chain), `tests/integration/test_audit_api.py` (query/verify/timeline), `tests/integration/test_audit_run_path.py` (runs and approvals audit themselves), `tests/integration/test_approvals.py` (quorum/expiry) | OK |
| O6 | Software engineering practice | this matrix, `docs/issues/BACKLOG.md`, `.github/workflows/ci.yml`, `docs/PROPOSAL_APPROVAL.md` | OK (baseline) |
| O7 | Measured quality | coverage gate (pyproject), CI chain, `docs/PERF_REPORT.md` (Phase 9 item 86) | Phase 9 |

## 2. Functional requirements → items, modules, tests

| FR | Requirement | TODO item(s) | Module(s) | Test evidence | State |
|---|---|---|---|---|---|
| FR-01 | Password hash + short-lived JWT, no plaintext secrets | 11 | `src/app/auth.py`, `src/app/db.py` (PBKDF2) | `tests/integration/test_app_auth.py` | OK (env-admin); DB users → 11 |
| FR-02 | RBAC at API **and** service layers | 12, 13, 16, 34 | `src/app/auth.py`, `src/app/main.py`, `src/safety/policy_service.py` | `tests/integration/test_app_rbac.py`, `tests/unit/test_policy_context.py` | OK (API deps + policy-service role check) |
| FR-03 | Workflow authoring from YAML/API with name/version/triggers/nodes/edges/policy | 18, 19, 27 | `src/engine/dsl/loader.py`, `src/app/models.py` | `tests/unit/test_engine_phase1.py` | partial |
| FR-04 | Full validation (schema, contracts, dup IDs, edges, placeholders, triggers, Kahn, dangerous-node) with line/col | 20–23, 25, 26 | `src/engine/validator/validator.py`, `errors.py`, `src/app/workflows_api.py` | `tests/unit/test_engine_phase1.py::ValidatorFixtureTests`, `tests/integration/test_workflows_validate.py::PreciseErrorResponseTests` | OK |
| FR-05 | Immutable versions; execution pins `(workflow_id, version)` | 27, 28 | `src/app/models.py` | `tests/integration/test_workflow_store.py`, `tests/integration/test_execution_store.py` | OK |
| FR-06 | Offline planner → typed plan through the same validator/policy path | 55–63 | `src/orchestration/planner.py` | `tests/unit/test_planner_varied_phrasing.py`, `test_agent_conversation.py` | partial; Phase 6 |
| FR-07 | Policy returns exactly ALLOW / NEED_APPROVAL / DENY + score/reasons/quorum/policy version | 33, 34 | `src/safety/policy_service.py`, `src/engine/executor/gate.py` | `tests/unit/test_policy_service.py`, `test_policy_context.py`, `test_policy_adversarial.py` | OK |
| FR-08 | Approval request, quorum, expiry, final decision | 35 | `src/app/approvals.py` | `tests/integration/test_approvals.py` | OK |
| FR-09 | Typed events with schema, correlation ID, idempotency key; duplicate-safe | 37–39, 44 | `src/bus/events.py`, `src/bus/triggers.py` | `tests/unit/test_bus_events.py`, `test_bus_idempotency.py`, `test_bus_triggers.py`, `tests/integration/test_bus_integration.py` | OK |
| FR-10 | DAG scheduling: dependencies, concurrency, propagation, timeout/retry, compensation | 29–32 | `src/engine/executor/{scheduler,gate,compensate,result}.py` | `tests/unit/test_engine_phase2.py` (parallel, retry, skip, timeout, compensate, kill-mid-run), `tests/unit/test_engine_gate.py` (compiled-node gate denies before dispatch) | OK |
| FR-11 | Typed worker results; no raw secrets/unbounded output persisted | 40, 43 | `src/bus/workers/core.py`, `src/app/models.py` | `tests/unit/test_bus_workers.py`, `test_bus_persist.py` | OK (bounded summaries; redaction → 85) |
| FR-12 | Append-only hash-chain audit; tamper detection pinpoints first broken link | 46–48, 54 | `src/storage/audit.py`, `event_logger.py`, `src/bus/workers/dispatcher.py`, `src/app/approvals.py` | `tests/unit/test_audit_chain.py` (canonical hash, tamper/relink/deleted row), `tests/unit/test_audit_trail.py` (every event category), `tests/integration/test_audit_api.py::…verify…`, `tests/integration/test_audit_run_path.py` | OK |
| FR-13 | Replay with pinned version + correlation ID; report match vs original | 52, 53, 54 | `src/app/replay.py`, `src/engine/executor/replay.py`, `src/bus/workers/dispatcher.py` | `tests/integration/test_replay.py` (pinning, tampered pin, RBAC), `tests/unit/test_engine_replay.py` (normalisation), `tests/acceptance/test_replay.py` (W2 end-to-end) | OK |
| FR-14 | Consent + PII-scrubbed shared memory; filtered retrieval | 64–68, 71 | `src/storage/templates.py` → `src/memory/` | planned | planned(64–68) |
| FR-15 | Reputation/quarantine gates reuse | 69, 70 | planned `src/memory/` | planned | planned(69,70) |
| FR-16 | Structured logs + metrics (count, success, latency, retries, approval time, replay, hits, decisions) | 51, 72 | `src/storage/metrics.py`, `run_metrics.py`, `event_logger.py` | `tests/unit/test_run_metrics.py`, `tests/integration/test_audit_api.py::…metrics…`, `tests/unit/test_p4_services.py::MetricsTests` | OK (51); memory hit-rate → 72 |

## 3. Non-functional requirements → verification

| NFR | Target | Verification | State |
|---|---|---|---|
| Performance | API p95 < 300 ms | repeatable HTTP benchmark (item 86) | planned(86) |
| Reliability | at-least-once + idempotent workers, no duplicate side effect | duplicate-delivery test (item 45) | OK (`tests/integration/test_bus_integration.py`, `tests/unit/test_bus_idempotency.py`) |
| Maintainability | type hints, focused modules | `mypy src`, `ruff check .` in CI (items 6, 7) | OK |
| Testability | ≥80% on planner/validator/policy/scheduler/audit | coverage floor in `pyproject.toml`; strict core gate command documented there | core gate `src/engine|safety|storage|bus` = 85.3% (≥80) as of item 54; global ratchet 63% → raise with item 63/91 |
| Security | no arbitrary unapproved command; Bandit + pip-audit pass | `tests/unit/test_execution_safety.py` adversarial cases; CI `bandit -ll`, `pip-audit` | OK (medium+ = 0 findings) |
| Privacy | PII scrub, consent/erasure tests | items 65, 71 | planned(65,71) |
| Usability | README demo < 10 min | README quickstart rehearsal (Phase 9 item 87/88) | baseline OK, study → 87 |
| Deployability | one compose file | `docker compose up --build` (verified image build in item 7) | partial → 88 |
| Recovery | worker crash/retry/DLQ observable | chaos tests (items 45, 88) | partial (kill-mid-run test OK) |

## 4. How to keep this matrix honest

1. Every commit references its TODO item `[N]`; every closed item updates the
   row(s) above with the real test path.
2. Phase 10 (item 91) regenerates this file from `docs/issues/BACKLOG.md` and
   the collected `--cov` report, and freezes it for the defense.
3. A requirement may not be marked covered by a test that does not exist in
   `tests/` — planned rows are explicitly labelled.
