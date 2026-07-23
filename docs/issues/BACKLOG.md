# Issue backlog — proposal section 15 TODO (items 1–98)

Each row is one GitHub issue. `#` is the proposal item number and the stable
issue ID; commits reference it as `[N]`. Owner `A` = Amir, `R` = Alireza.

## Phase 0 — Scope, repository and quality baseline (gate: install → tests → health endpoint)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 1 | Approve proposal and freeze MVP scope | both | phase-0, area:docs |
| 2 | Create GitHub issues from this TODO and assign owners | A | phase-0, area:docs |
| 3 | Record external source/license inventory | A | phase-0, area:docs |
| 4 | Define coding, branching, review and commit conventions | both | phase-0, area:docs |
| 5 | Add `tests/` structure for unit, integration and acceptance tests | A | phase-0, area:qa |
| 6 | Add formatter, linter, type checker and coverage configuration | A | phase-0, area:qa |
| 7 | Add CI workflow for tests and Docker build | A | phase-0, area:qa |
| 8 | Update README with clean setup and architecture | R | phase-0, area:docs |
| 9 | Create requirements traceability matrix | R | phase-0, area:docs |

## Phase 1 — API, authentication and database foundation (gate: RBAC protects routes; clean DB migrates)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 10 | Finalize settings and environment validation | R | phase-1, area:api |
| 11 | Implement password hashing and short-lived JWT | R | phase-1, area:api |
| 12 | Implement OWNER, OPERATOR, APPROVER and VIEWER roles | R | phase-1, area:api |
| 13 | Add authorization dependencies to protected routes | R | phase-1, area:api |
| 14 | Add teams, members and workspaces | R | phase-1, area:storage |
| 15 | Add reproducible migration runner | R | phase-1, area:storage |
| 16 | Add repository interfaces and transaction boundaries | R | phase-1, area:storage |
| 17 | Add migration and RBAC integration tests | A | phase-1, area:qa |

## Phase 2 — Workflow DSL and validator (gate: invalid workflows never persisted)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 18 | Freeze the five node types and three trigger types | A | phase-2, area:engine |
| 19 | Implement typed YAML loader with source locations | A | phase-2, area:engine |
| 20 | Implement schema and parameter validation | A | phase-2, area:engine |
| 21 | Implement duplicate ID, edge and placeholder checks | A | phase-2, area:engine |
| 22 | Implement Kahn cycle detection and deterministic topological order | A | phase-2, area:engine |
| 23 | Implement static dangerous-node approval validation | A | phase-2, area:safety |
| 24 | Add at least ten valid/invalid fixtures | A | phase-2, area:qa |
| 25 | Expose `POST /workflows/validate` | R | phase-2, area:api |
| 26 | Add precise validation error responses (stage/message/line/column) | R | phase-2, area:api |

## Phase 3 — DAG executor, policy and approvals (gate: 3-node DAG runs; policy cannot be bypassed)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 27 | Add workflow/version persistence | R | phase-3, area:storage |
| 28 | Add execution and execution-step persistence | R | phase-3, area:storage |
| 29 | Implement READY/RUNNING/SUCCEEDED/FAILED/BLOCKED states | A | phase-3, area:engine |
| 30 | Implement concurrency for independent nodes | A | phase-3, area:engine |
| 31 | Implement timeout, retry and failure propagation | A | phase-3, area:engine |
| 32 | Implement compensation contract | A | phase-3, area:engine |
| 33 | Extract policy decisions into a dedicated service | R | phase-3, area:safety |
| 34 | Add workspace, role, path and command checks | R | phase-3, area:safety |
| 35 | Implement approval quorum and expiration | R | phase-3, area:storage |
| 36 | Add policy boundary and adversarial tests | A | phase-3, area:qa |

## Phase 4 — Redis event bus and workers (gate: W2 through the bus; duplicates are safe)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 37 | Freeze event JSON schemas | A | phase-4, area:bus |
| 38 | Implement Redis Streams producer and consumer group | A | phase-4, area:bus |
| 39 | Add correlation and idempotency key handling | A | phase-4, area:bus |
| 40 | Add shell and approval workers | A | phase-4, area:bus |
| 41 | Add GUI/custom contract workers as explicit non-production stubs | A | phase-4, area:bus |
| 42 | Implement retry backoff and dead-letter stream | A | phase-4, area:bus |
| 43 | Persist step state before acknowledging events | A | phase-4, area:bus |
| 44 | Implement manual, file-arrival and schedule triggers | A | phase-4, area:bus |
| 45 | Add duplicate-delivery, ordering and DLQ tests | A | phase-4, area:qa |

## Phase 5 — Audit, metrics and replay (gate: W1 and W2 produce inspectable evidence)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 46 | Add append-only audit repository | R | phase-5, area:storage |
| 47 | Add canonical payload hashing and hash-chain verification | R | phase-5, area:storage |
| 48 | Record policy, approval, dispatch, retry, result and replay events | R | phase-5, area:storage |
| 49 | Add audit query endpoint with pagination | R | phase-5, area:api |
| 50 | Add execution timeline endpoint | R | phase-5, area:api |
| 51 | Add metrics for success, latency, retries and approval time | R | phase-5, area:storage |
| 52 | Implement replay with source-version pinning | A | phase-5, area:engine |
| 53 | Implement normalized output comparison | A | phase-5, area:engine |
| 54 | Add tamper and replay tests | A | phase-5, area:qa |

## Phase 6 — Offline planner and optional LLM adapter (gate: planner output passes validator/policy)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 55 | Define supported intents and slots | A | phase-6, area:planner |
| 56 | Create and version a small labeled dataset | A | phase-6, area:planner |
| 57 | Implement normalization and high-precision rules | A | phase-6, area:planner |
| 58 | Implement slot extraction and canonical task schema | A | phase-6, area:planner |
| 59 | Add confidence threshold and clarification response | A | phase-6, area:planner |
| 60 | Add typed LLM adapter behind an interface | A | phase-6, area:planner |
| 61 | Add timeout, retry, provider failure and invalid-response handling | A | phase-6, area:planner |
| 62 | Record provider metadata without storing secrets | A | phase-6, area:planner |
| 63 | Measure precision, recall, F1, latency and fallback rate | R | phase-6, area:qa |

## Phase 7 — Memory, consent and reputation (gate: safe reuse works; poisoned plan cannot)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 64 | Implement consent records and revocation | R | phase-7, area:memory |
| 65 | Implement PII scrub before persistence | R | phase-7, area:memory |
| 66 | Add deterministic local embeddings for tests | R | phase-7, area:memory |
| 67 | Add similarity retrieval and top-k ranking | R | phase-7, area:memory |
| 68 | Filter by risk, OS compatibility and quarantine state | R | phase-7, area:memory |
| 69 | Implement reputation formula and update rules | R | phase-7, area:memory |
| 70 | Implement QUARANTINED, SANDBOX and PUBLIC states | R | phase-7, area:memory |
| 71 | Add erasure endpoint and erasure integration test | A | phase-7, area:api |
| 72 | Add reuse-hit and no-generation-cost metrics | A | phase-7, area:qa |

## Phase 8 — Sandbox and client experience (gate: nothing writes outside its workspace)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 73 | Define sandbox request/response contract | A | phase-8, area:engine |
| 74 | Enforce timeout, no network, restricted filesystem and resource limits | A | phase-8, area:engine |
| 75 | Add plugin manifest validation | A | phase-8, area:engine |
| 76 | Run custom nodes sandbox-first | A | phase-8, area:engine |
| 77 | Add Tkinter screens for workflow, run, approval and audit views | R | phase-8, area:ui |
| 78 | Add human-readable policy explanations | R | phase-8, area:ui |
| 79 | Add a safe dry-run mode | A | phase-8, area:engine |
| 80 | Add sandbox contract and UI walkthrough tests | A | phase-8, area:qa |

## Phase 9 — Security, usability and deployment hardening (gate: NFRs have measured evidence)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 81 | Complete STRIDE threat model | R | phase-9, area:security |
| 82 | Run Bandit, dependency audit and secret scan | R | phase-9, area:security |
| 83 | Remove development default secrets from production configuration | R | phase-9, area:security |
| 84 | Add rate limits and request validation where needed | R | phase-9, area:api |
| 85 | Add structured log redaction | R | phase-9, area:security |
| 86 | Benchmark API p95 and worker throughput | R | phase-9, area:qa |
| 87 | Run SUS study with five users | R | phase-9, area:ui |
| 88 | Verify clean compose deployment from a new machine | A | phase-9, area:qa |
| 89 | Test backup/restore and migration upgrade path | A | phase-9, area:qa |

## Phase 10 — Final SE package and defense rehearsal (gate: full requirement→code→test→demo traceability)

| # | Issue | Owner | Labels |
|---|---|---|---|
| 90 | Complete UML and architecture diagrams | R | phase-10, area:docs |
| 91 | Complete requirements-to-test traceability | R | phase-10, area:docs |
| 92 | Freeze release candidate and tag it | both | phase-10, area:qa |
| 93 | Export test, coverage, security and performance reports | A | phase-10, area:qa |
| 94 | Write limitations and deferred-feature section | R | phase-10, area:docs |
| 95 | Prepare three-minute backup video | both | phase-10, area:docs |
| 96 | Rehearse W1, W2 and optional W3 from a clean environment | both | phase-10, area:qa |
| 97 | Prepare answers for safety, AI ownership, reuse, failure recovery and scalability | both | phase-10, area:docs |
| 98 | Archive prompt logs and human review checklists | both | phase-10, area:docs |
