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
| 4 | Redis event bus and workers | TODO | — |
| 5 | Audit, metrics and replay | TODO | — |
| 6 | Offline planner and optional LLM adapter | TODO | — |
| 7 | Memory, consent and reputation | TODO | — |
| 8 | Sandbox and client experience | TODO | — |
| 9 | Security, usability and deployment hardening | TODO | — |
| 10 | Final SE package and defense rehearsal | TODO | — |

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
