# Requirements-to-test traceability (generated)

**Item:** Phase 10 — [91] Complete requirements-to-test traceability  
**Generated:** 2026-10-18T12:17:44+00:00 by `scripts/traceability_report.py`  
**Scope:** 25 requirements, 47 test files, 619 collected tests

Regenerate with `python scripts/traceability_report.py`; validate without writing with `--check`. A hand-edited copy of this file is a claim, not evidence — the script asserts every module and test file exists and collects at least one test.

## 1. Functional requirements (proposal §6)

| ID | Requirement | Implementation | Test evidence | Collected |
|---|---|---|---|---:|
| FR-01 | Password hashing and short-lived JWT, no plaintext secrets | `src/app/auth.py`, `src/app/security.py`, `src/app/db.py` | `tests/integration/test_app_auth.py`, `tests/unit/test_app_security.py` | 18 |
| FR-02 | RBAC enforced at the API layer and in the services | `src/app/deps.py`, `src/app/roles.py`, `src/safety/policy_service.py` | `tests/integration/test_app_rbac.py`, `tests/unit/test_policy_context.py` | 22 |
| FR-03 | Typed YAML DSL with name, version, triggers, nodes, edges, policy | `src/engine/dsl/loader.py`, `src/engine/dsl/compiler.py` | `tests/unit/test_engine_phase1.py`, `tests/integration/test_workflow_store.py` | 49 |
| FR-04 | Full validation with precise line/column findings | `src/engine/validator/validator.py`, `src/app/workflows_api.py` | `tests/integration/test_workflows_validate.py`, `tests/unit/test_engine_phase1.py` | 55 |
| FR-05 | Immutable versions; a run pins (workflow_id, version) | `src/app/models.py` | `tests/integration/test_workflow_store.py`, `tests/integration/test_execution_store.py` | 16 |
| FR-06 | Offline planner into a typed plan through the shared validator/policy | `src/orchestration/offline_planner.py`, `src/orchestration/plan_compiler.py` | `tests/unit/test_planner_rules.py`, `tests/unit/test_plan_compiler.py`, `tests/acceptance/test_planned_workspace.py` | 38 |
| FR-07 | Policy returns exactly ALLOW / NEED_APPROVAL / DENY with score and reasons | `src/safety/policy_service.py`, `src/safety/explanations.py`, `src/safety/dryrun.py`, `src/app/policy_api.py` | `tests/unit/test_policy_service.py`, `tests/unit/test_policy_adversarial.py`, `tests/unit/test_policy_explanations.py`, `tests/integration/test_dryrun_api.py` | 45 |
| FR-08 | Approval request, quorum, expiry, final decision | `src/app/approvals.py`, `src/app/approvals_api.py` | `tests/integration/test_approvals.py`, `tests/integration/test_approvals_api.py` | 27 |
| FR-09 | Typed events with correlation and idempotency keys; duplicates safe | `src/bus/events.py`, `src/bus/triggers.py` | `tests/unit/test_bus_events.py`, `tests/unit/test_bus_idempotency.py`, `tests/integration/test_bus_integration.py` | 19 |
| FR-10 | DAG scheduling with concurrency, propagation, timeout, retry, compensation | `src/engine/executor/scheduler.py`, `src/engine/executor/compensate.py` | `tests/unit/test_engine_phase2.py`, `tests/unit/test_engine_concurrency.py`, `tests/unit/test_engine_retry.py` | 18 |
| FR-11 | Typed worker results; no raw secrets or unbounded output persisted | `src/bus/workers/core.py`, `src/app/models.py` | `tests/unit/test_bus_workers.py`, `tests/unit/test_bus_persist.py` | 10 |
| FR-12 | Append-only hash-chain audit with tamper detection | `src/storage/audit.py`, `src/app/audit_api.py` | `tests/unit/test_audit_chain.py`, `tests/unit/test_audit_trail.py`, `tests/integration/test_audit_api.py`, `tests/integration/test_audit_run_path.py` | 32 |
| FR-13 | Replay from the pinned version with normalized output comparison | `src/app/replay.py` | `tests/integration/test_replay.py`, `tests/acceptance/test_replay.py` | 15 |
| FR-14 | Consent-gated, PII-scrubbed shared memory with filtered retrieval | `src/memory/store.py`, `src/memory/retrieval.py`, `src/app/memory_api.py` | `tests/unit/test_memory_scrub.py`, `tests/unit/test_memory_retrieval.py`, `tests/integration/test_memory_api.py` | 38 |
| FR-15 | Reputation and quarantine gate reuse | `src/memory/reputation.py`, `src/memory/states.py` | `tests/unit/test_memory_reputation.py`, `tests/acceptance/test_memory_reuse_gate.py` | 20 |
| FR-16 | Structured logs and metrics for runs, retries, approvals, replay, memory | `src/storage/metrics.py`, `src/storage/run_metrics.py`, `src/app/logging.py` | `tests/unit/test_run_metrics.py`, `tests/unit/test_log_redaction.py`, `tests/integration/test_audit_api.py` | 40 |

## 2. Non-functional areas (proposal §7)

| Area | Target | Evidence | Tests | State |
|---|---|---|---:|---|
| Performance | API p95 < 300 ms excluding LLM calls | `scripts/benchmark.py` — measured worst p95 33.92 ms — `docs/PERF_REPORT.md` | 0 | proven |
| Reliability | At-least-once delivery, idempotent workers, no duplicate side effect | `src/bus/consumer.py`, `src/bus/workers/core.py` | 10 | proven |
| Maintainability | Type hints, focused modules, lint and type gates | `pyproject.toml`, `.github/workflows/ci.yml` — `ruff check .`, `ruff format --check .`, `mypy src` in CI | 0 | proven |
| Testability | Coverage floor on the critical packages | `pyproject.toml` — core `engine|safety|storage|bus` 86.1%, global 80.75%, floor 78% | 0 | proven |
| Security | No unapproved command; static analysis and dependency audit clean | `docs/SECURITY_REPORT.md`, `docs/THREAT_MODEL.md`, `scripts/secret_scan.py` | 52 | proven |
| Privacy | Consent, PII scrubbing and erasure | `src/memory/scrub.py`, `src/memory/consent.py` | 26 | proven |
| Usability | A first-time user completes the five client tasks; SUS recorded | `docs/USABILITY_STUDY.md`, `scripts/sus_score.py` — protocol and scorer ready — five sessions not yet run | 30 | PENDING |
| Deployability | One compose file that works from a clone; production refuses dev secrets | `compose.yml`, `compose.prod.yml`, `docs/DEPLOYMENT.md` | 13 | proven |
| Recovery | Crash/retry/DLQ observable; backup/restore and upgrade proven | `scripts/backup.py`, `src/engine/executor/states.py` — no full chaos suite (see `docs/LIMITATIONS.md`) | 26 | proven |

## 3. Honest gaps

| Gap | Where it is recorded |
|---|---|
| Usability study not run (Usability) | `docs/USABILITY_STUDY.md` §6 |
| No dynamic/penetration testing; static analysis only | `docs/SECURITY_REPORT.md` §4 |
| No full chaos suite (kill-mid-run covered) | `docs/LIMITATIONS.md` |
| Deployment verified on one host, not a second machine | `docs/DEPLOYMENT.md` §5 |
| PostgreSQL is in the topology but not the application store | `docs/LIMITATIONS.md` |

## 4. Validation

All cited modules and test files exist, every referenced test file collects at least one test, and every `FR-01…FR-16` id plus all nine non-functional areas are mapped.
