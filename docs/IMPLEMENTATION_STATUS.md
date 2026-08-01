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
| 6 | Offline planner and optional LLM adapter | TODO | — |
| 7 | Memory, consent and reputation | TODO | — |
| 8 | Sandbox and client experience | TODO | — |
| 9 | Security, usability and deployment hardening | TODO | — |
| 10 | Final SE package and defense rehearsal | TODO | — |

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
