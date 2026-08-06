# Limitations and deferred features

**Item:** Phase 10 — [94] Write the limitations and deferred-feature section.
**Scope:** the frozen release candidate (`docs/RELEASE_NOTES.md`). Everything
here is deliberate: it is either an MVP boundary from the proposal, a gap
found while building, or a known hazard. Each entry says what it costs, what
compensates for it today, and where the fix belongs.

The rule this document follows: a limitation may only be listed if the
project *knows* it — a guess is not a limitation, it is a missing test.

**Removed since the `v1.0.0-rc1` tag (item [99], 2026-10-18):** `shell` nodes
used to be *record-only* — the worker recorded the command it would have run.
They now execute for real, inside the sandbox, in the run's workspace, with the
policy gate enforcing the workspace boundary before dispatch. The row that was
here is gone because the tests that prove the new behaviour exist:
`tests/integration/test_shell_execution.py` (real files created, path escape
denied pre-dispatch, timeout kills the step, deterministic step output) and
`tests/unit/test_sandbox_commands.py` (refused without OS isolation).

---

## 1. Functional boundaries (deliberate MVP scope)

| Limitation | Impact | Compensation today | Where it would be fixed |
|---|---|---|---|
| No visual drag-and-drop workflow editor | workflows are authored as YAML text (the client's Workflow tab validates and dry-runs) | validator reports line/column findings, dry run explains every node | post-MVP editor over the same API |
| No VLM/GUI automation implementation | `gui_click`/`gui_type` nodes are validated, gated and dispatched to explicit **stub** workers | they are visibly non-production stubs, and the contract is frozen so an adapter cannot change the DSL | `STRETCH` GUI adapter behind the frozen worker contract |
| A `shell` command needs OS isolation (`bwrap` + user namespaces) | on a host that cannot create namespaces, a `shell` step **fails** instead of running | the step fails closed with the sandbox's reason on the audit chain, and the plugin path still runs under the audit-hook layer | a runtime profile (seccomp/gVisor) that can isolate commands without unprivileged user namespaces |
| Planner covers 9 intents | unusual requests fall back to a clarification instead of a plan | explicitly measured (`docs/PLANNER_EVAL.md`: accuracy 1.000 on the frozen dataset, fallback rate 0.111) and the fallback is a first-class response | more intents + a wider dataset |
| Optional LLM adapter is off by default | no provider calls in the demo or CI | the offline planner is deterministic and always available | configure a provider; the typed adapter and its failure handling are already tested |
| Shared memory is consent-scoped per user | no cross-team knowledge sharing policy | consent grants/revocations are recorded and enforced; erasure keeps the consent history | workspace-scoped sharing policy |
| No marketplace / plugin publishing | plugins are registered from a directory by an operator | manifest validation + registry refuse unknown, duplicate or mis-declared plugins | signed plugin bundles |
| No OAuth / SSO / external identity | users are env-admin plus DB-created users with local passwords | PBKDF2-SHA256 hashes, short-lived JWTs, RBAC on every route | OIDC provider adapter |
| No token revocation list | a stolen token is valid until it expires (default 60 min) | short TTL is configurable; the signing key can be rotated per deployment | refresh tokens + denylist |

## 2. Architectural limits

| Limitation | Impact | Compensation today | Where it would be fixed |
|---|---|---|---|
| SQLite is the application store (single writer) | concurrent writes serialise; no horizontal API scaling | low write volume, short transactions, snapshots via `scripts/backup.py` | the Phase 9+ SQLAlchemy/PostgreSQL migration (`asyncpg` is already pinned) |
| PostgreSQL is deployed but not used by the app | the compose `db` service is topology, not the store | documented in `docs/DEPLOYMENT.md`; the schema runner already supports DB-API connections | migration above |
| Rate limiting is per process | N API replicas allow N× the configured rate | generous defaults, login bucket refunded on success, `Retry-After` returned | edge limiter or a shared (Redis) bucket |
| One audit chain per execution | no single global chain ordering all events | every chain is independently verifiable and the run timeline joins steps and events | global Merkle/anchor scheme |
| No external anchoring of the audit chain | a host-level attacker who rewrites the whole chain cannot be detected | tamper detection catches partial edits, deletions and relinks (`tests/unit/test_audit_chain.py`) | daily signed digest published elsewhere |
| Sandbox depends on `bwrap` for `command` nodes | without user namespaces, only Python plugins run | the audit-hook layer still denies escape attempts and `command` is refused rather than run unguarded | seccomp/profile-based runtime (gVisor, Firecracker) |
| Sandbox is namespace-level isolation, not a hardened container runtime | kernel-level escapes are out of its threat model | defence in depth: fresh interpreter, audit hook, resource limits, sentinel directory, no host bind mounts | dedicated runner host or microVM |
| `command`-kind sandbox requests must declare a body length | a chunked upload is rejected (411) | JSON-only API by design | streaming-aware limiter if uploads are ever needed |
| `shell` execution inside the Docker container needs an opt-in | Docker's default seccomp profile blocks the namespace syscalls, so in the default compose stack a `shell` step fails closed ("needs OS isolation"); with `security_opt: [seccomp=unconfined]` it runs and reports `bwrap-no-proc` (no private `/proc` inside a container, measured) | `compose.yml` documents the opt-in; the failure is a precise, audited step error, never a silent no-op; `docs/DEPLOYMENT.md` §4 records both measurements | a container runtime profile that allows user namespaces without disabling seccomp, or a dedicated worker host |

## 3. Observability and operations

| Limitation | Impact | Compensation today | Where it would be fixed |
|---|---|---|---|
| No Prometheus/tracing/alerting integration | metrics are pull-based JSON (`/metrics/executions`) and structured logs | JSON access logs with redaction, per-run metrics, audit chain | exporter + dashboards |
| No TLS/ingress in the repository | the compose stack speaks plain HTTP | `compose.prod.yml` publishes only `127.0.0.1:8000` and the docs require TLS at the edge | reverse proxy or ingress config |
| One compose file, single host | no orchestration, no autoscaling, no blue/green | health checks, restart policies, named volumes | Kubernetes or a managed platform (`STRETCH`) |
| Migrations are forward-only | a bad migration must be undone by a new migration | checksummed history, drift detection, `scripts/backup.py` snapshots taken before changes | guarded down-migrations |
| Backup/restore is a manual command | recovery depends on an operator running it | `scripts/backup.py` verifies the snapshot and the manifest, `docs/DEPLOYMENT.md` is the runbook | scheduled job + off-host copy |
| Deployment verified on **one** host | "works on a new machine" rests on a clean build with clean volumes on the dev machine | reproducible image, pinned dependencies, no host-specific paths | repeat the item [88] checklist on the demo laptop |

## 4. Testing gaps

| Gap | Why it matters | Current state |
|---|---|---|
| Usability study not run (item [87]) | the only NFR without measured evidence | protocol, tasks, instrument and a tested scorer exist (`docs/USABILITY_STUDY.md`); the five sessions are pending |
| No dynamic security testing, fuzzing or penetration test | static analysis cannot find logic-level abuse | Bandit, `pip-audit`, secret scan and the STRIDE model cover the static side (`docs/SECURITY_REPORT.md`) |
| No full chaos suite (broker/DB/worker kill loops) | recovery claims rest on retry/DLQ and one kill-mid-run test | idempotency, backoff, DLQ and persist-before-ack are tested; broker-down behaviour is not |
| GUI widgets are not automated | `build_window` needs a display, so wiring bugs are not caught in CI | the view model and HTTP client are covered by a live-API walkthrough (`tests/acceptance/test_workbench_walkthrough.py`); the widget tree is exercised manually |
| Redis-backed tests skip without a broker | the compose run does not exercise Redis Streams | the same suite runs gated on a reachable Redis; the contract is verified against the ephemeral clone when available |
| No performance regression gate in CI | p95 is measured on demand, not per commit | `scripts/benchmark.py` exits non-zero when the 300 ms target is missed, so it *can* be wired into CI |
| Two tests skip in this environment | the suite is not 100% executed here | reported as skipped, never as passed |

## 5. Privacy and data handling

| Limitation | Impact | Compensation today | Where it would be fixed |
|---|---|---|---|
| PII scrubbing is rule-based | unusual formats may slip through; ordinary words can be over-redacted | the rule table is frozen, tested, and the store refuses content that still matches a rule; logs use the same rules | ML-assisted detection with the same fail-closed contract |
| No retention/expiry policy engine | data stays until it is erased explicitly | erasure endpoint removes item + events and keeps the consent history | scheduled retention jobs |
| Logs contain a peer-address **digest**, not the address | incident forensics cannot recover the raw IP from logs | correlation per client is preserved; the limiter still uses the raw address in memory | deliberate trade-off; document in the runbook if policy requires raw addresses |
| Memory embeddings are hashing-based, not semantic | retrieval is token-overlap, so paraphrases may be missed | deterministic, dependency-free and testable; reputation/quarantine keep bad plans out | pluggable embedding adapter |

## 6. Platform and accessibility

| Limitation | Impact | Compensation today |
|---|---|---|
| Linux-first | sandboxing, `bwrap` and some shell tooling are POSIX-specific; Windows/macOS are not verified | the API and client are stdlib-only and platform-neutral; tests run on Linux |
| Tkinter client | no screen-reader/accessibility audit; theming is default | keyboard-navigable widgets, plain-text screens, and the API is fully usable without the GUI (`curl`/workbench client) |
| English-only UI and messages | non-English operators fall back to raw error codes | messages are short and structured (`stage:line:column`), so they can be localized by a mapping table |

## 7. Deferred (stretch) features

Marked `STRETCH` in code and docs, excluded from the MVP acceptance path:

- visual drag-and-drop workflow IDE;
- VLM-based screen control and cross-platform GUI automation;
- OAuth/SSO and multi-tenant organizations beyond workspaces;
- plugin marketplace with signed bundles;
- Kubernetes deployment, autoscaling and multi-region;
- any non-Linux runtime target for the sandbox.

## 8. How a limitation gets added or removed

1. **Added** when a review, a test or the usability study exposes a gap that
   is not fixed in this release: one row, with impact, compensation and the
   item that would fix it.
2. **Removed** only together with the test (or measured evidence) that proves
   the fix — deleting a row without evidence moves a known gap into an
   unknown one.
3. `docs/TRACEABILITY.md` §4 keeps the "areas without measured evidence" list
   in sync; today that list contains exactly one entry: the usability study.
