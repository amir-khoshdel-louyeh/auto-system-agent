# Defense Q&A — prepared answers (item [97])

**How to use this:** the presenter reads the **answer** aloud (≈30 seconds),
then points at the **evidence** if the examiner wants proof. Every number here
was produced by a command in `docs/reports/`; nothing is quoted from memory.

| Group | Question | Answer |
|---|---|---|
| Safety | §1–§5 | deterministic policy, fail-closed gate, sandbox, approvals, audit |
| AI ownership | §6–§9 | who wrote the code, how it is verified, what happens when it is wrong |
| Reuse | §10–§12 | third-party inventory, runtime plan reuse |
| Failure recovery | §13–§16 | idempotency, retry/DLQ, compensation, replay, backup |
| Scalability | §17–§19 | measured throughput, scale-out path, what breaks first |

---

## 1. "AI can be wrong — how do you stop it executing something dangerous?"

**Answer.** The model is never in the execution path. A request becomes a
*typed plan document*, and that document goes through exactly the same loader,
5-stage validator and policy service as a hand-written YAML workflow. The
policy service is deterministic and returns one of `ALLOW` / `NEED_APPROVAL` /
`DENY` with a score, reasons, required quorum and policy version; the scheduler
and the worker both consult it, and a `DENY` never reaches a worker. There is no
code path from "model said so" to "command ran".

**Evidence.** `src/orchestration/plan_compiler.py` (the only way a plan enters
the engine), `src/safety/policy_service.py`, `src/engine/executor/gate.py`,
`tests/unit/test_policy_adversarial.py`, `tests/acceptance/test_planned_workspace.py`.

## 2. "Show me a case where the planner produced something dangerous."

**Answer.** We keep one as a test: the request that compiles to `rm -f /etc/passwd`
is *planned*, then gated `DENY` — the planner is allowed to propose, the policy
refuses, and the refusal is on the audit chain. The same rule blocks
`rm -rf ../outside`, which the dry-run endpoint blocks while persisting nothing.

**Evidence.** `tests/acceptance/test_planned_workspace.py`,
`tests/unit/test_policy_adversarial.py`, `tests/integration/test_dryrun_api.py`
(`persisted=False`, `dispatched=False`).

## 3. "What if the gate itself is bypassed or misused?"

**Answer.** Defence in depth, and one bug found this way is now a test: the
compiled DAG nests node parameters (`{"type": "shell", "params": {"run": …}}`)
while the policy service reads a flat spec — a scheduler that forgot to flatten
would gate an *empty* command and let anything through. `gate.flatten_node`
normalises before deciding, the gate **fails closed** on anything it cannot
read, and the shell worker re-runs the gate on its own copy of the spec before
touching the node. Bandit is clean at medium+ and `pip-audit` reports no known
vulnerabilities on all three requirement sets.

**Evidence.** `src/engine/executor/gate.py` (item [34] docstring),
`src/bus/workers/shell.py`, `docs/reports/bandit-summary.json`,
`docs/reports/pip-audit.txt`, `docs/SECURITY_REPORT.md`.

## 4. "Untrusted plugin code — why should I believe the sandbox holds?"

**Answer.** Because we wrote the attempt and it fails inside the test suite:
escape with `../`, an absolute path, a symlink, a delete outside the workspace,
reading `/etc/passwd`, `subprocess`, a socket — each is denied, and the
directory outside the workspace is byte-identical afterwards. Two layers is the
point: `bwrap` namespaces where user namespaces are available, and a Python
audit hook as the tested fallback; if neither can isolate the request, a
`command` node is **refused** rather than run unguarded. Resource limits
(wall-clock, memory, file size) kill the hog cases.

**Evidence.** `tests/acceptance/test_sandbox_isolation.py`,
`src/sandbox/runner.py`, `src/sandbox/harness.py`, `docs/THREAT_MODEL.md` (the
residual risk — kernel-level escape — is stated, not hidden).

## 5. "Who can approve what, and can an approver approve themselves?"

**Answer.** Authorization is a frozen role matrix (`OWNER`, `OPERATOR`,
`APPROVER`, `VIEWER`) enforced at the API *and* in the policy service. A risky
node needs a configured quorum of distinct approver decisions before its expiry;
a decision on a resolved request is a `409`, not a silent no-op, and every
decision is appended to the same hash chain as the run it belongs to.

**Evidence.** `src/app/roles.py`, `src/app/approvals.py`,
`tests/integration/test_approvals.py`, `test_approvals_api.py`,
`test_audit_run_path.py`.

---

## 6. "Which parts of this project did an AI write, and how do you know they are correct?"

**Answer.** The assistant wrote a large share of the code, and the project
documents that instead of hiding it: every phase's prompts, the review each
commit went through and the human decision behind it are archived in
`docs/AI_USAGE_LOG.md`, with the per-commit checklist in
`docs/REVIEW_CHECKLISTS.md`. Correctness is not established by the AI's
confidence — it rests on the same controls a human author faces: 1055 tests,
coverage gates (global ≥78%, core `engine|safety|storage|bus` 86.12%), ruff,
mypy, Bandit, `pip-audit`, the adversarial suites, and a human review that may
reject a change.

**Evidence.** `docs/AI_USAGE_LOG.md`, `docs/REVIEW_CHECKLISTS.md`,
`docs/reports/export.txt` (12 gates, all PASS).

## 7. "What is *your* contribution if AI wrote the code?"

**Answer.** The engineering decisions and the discipline around them: the
frozen requirements and scope, the workflow language and its validator, the
deterministic policy model, the DAG scheduler and compensation contract, the
event contract with idempotency and DLQ, the memory quarantine/reuse rules,
the sandbox contract, the threat model, and the test/verification strategy that
hears "this generated code is wrong" from a failing test rather than a hunch.
The repository is the evidence: 98 numbered items, each with the commit that
closed it and the test that proves it (`docs/IMPLEMENTATION_STATUS.md`,
`docs/TRACEABILITY.md`).

## 8. "What happens when the AI is wrong? Give an example."

**Answer.** It is recorded as a bug with its test. Recent real ones: the shell
worker gated a nested spec and would have allowed a dangerous node (fixed by
flattening, item [34]); deploying the compose stack lost SQLite state because
the database lived in the container layer (found by item [88], fixed with a
named volume and a re-verification); the Bandit JSON artefact was unparseable
because of raw control characters (item [93], replaced by a CSV-derived summary
that a test now covers). Each is a commit plus a regression test, and the third
one is exactly why `docs/reports/` is generated by a script rather than typed
by hand.

**Evidence.** `tests/unit/test_engine_gate.py`, `docs/DEPLOYMENT.md` §2.2,
`scripts/bandit_summary.py` + `tests/unit/test_bandit_summary.py`.

## 9. "Is the project usable without the AI parts at all?"

**Answer.** Yes — that is the design constraint. The planner is a deterministic
normalize→rules→slots pipeline with a 99-utterance frozen dataset; the optional
LLM adapter is off by default, typed, schema-validated, timeout/retry/fallback
aware, and records secret-free provider metadata. With no provider configured,
every acceptance scenario still passes.

**Evidence.** `docs/PLANNER_EVAL.md`, `src/orchestration/offline_planner.py`,
`src/orchestration/llm_adapter.py`, `tests/unit/test_llm_adapter.py`.

---

## 10. "You copied from open-source projects — how much, and under which licences?"

**Answer.** The inventory is explicit and auditable. Eight reference projects
are vendored read-only under `source_MIT/` (gitignored, excluded from the
image); each has its upstream, licence and *permitted* use recorded, and two
with no licence file in the snapshot are marked copy-blocked (ideas only).
Nothing is imported at runtime — the required check is two greps that must find
no import of `source_MIT` — and logic adapted from a reference must pass the
same review, tests and attribution rules as project-written code.

**Evidence.** `docs/THIRD_PARTY_NOTICES.md`, `docs/SECURITY_REPORT.md` §1.1 (the
audited suppressions), `SE_Proposal_v4.tmp` §11, `docs/CONVENTIONS.md`.
**Limitation, stated up front:** two upstream URLs are unverified in the
snapshot and those two projects stay copy-blocked.

## 11. "What are your direct dependencies, and are they safe?"

**Answer.** The client and API are stdlib-only where it matters (Tkinter UI,
`urllib` client); the server adds FastAPI/uvicorn, Redis, SQLAlchemy/asyncpg
(pinned for the Postgres migration path, not yet used), plus a dev toolchain.
Versions are pinned to the audited set and `pip-audit` reports no known
vulnerabilities across `requirements.txt`, `requirements-api.txt` and
`requirements-dev.txt`.

**Evidence.** `requirements*.txt`, `docs/reports/pip-audit.txt`,
`docs/THIRD_PARTY_NOTICES.md` §B.

## 12. "Reuse is also a feature of yours — how does *runtime* reuse stay safe?"

**Answer.** Every stored plan is consent-gated and PII-scrubbed before
persistence, and reuse is filtered by risk, OS compatibility and quarantine
state *before* scoring. A failed plan stays `QUARANTINED` and cannot be
auto-reused; the only way out of quarantine is a successful sandboxed proof;
`QUARANTINED → SANDBOXED → PUBLIC` is the only promotion path. `GET /memory/metrics`
reports reuse hits and the planner calls they avoided.

**Evidence.** `src/memory/store.py`, `states.py`, `reputation.py`,
`tests/acceptance/test_memory_reuse_gate.py`,
`tests/integration/test_memory_api.py`.

---

## 13. "Redis delivers a message twice — what happens?"

**Answer.** Nothing twice. Each event carries a correlation id and an
idempotency key; the runner records processed keys, so a redelivered delivery is
acknowledged without re-running the handler. More importantly the step's
durable state is written **before** the verdict is published and therefore
before the delivery is acknowledged — a crash in that window can leave an extra
step row, never a lost effect. Test evidence: triggering one run twice yields a
single side effect.

**Evidence.** `src/bus/consumer.py`, `src/bus/workers/core.py` (item [43]),
`tests/unit/test_bus_idempotency.py`,
`tests/integration/test_bus_integration.py`, `test_bus_triggers.py`.

## 14. "A worker hangs or crashes mid-run."

**Answer.** Timeouts, bounded retries with exponential backoff, then the event
is parked in the dead-letter stream with the last error and the attempt count —
visible, not silent. Nodes that already succeeded are not silently abandoned:
the compensation contract runs their `compensate` in reverse order, and a node
whose dependency never succeeds becomes `BLOCKED` rather than pretending to
succeed. Kill-mid-run is an actual test.

**Evidence.** `src/engine/executor/scheduler.py`, `compensate.py`,
`states.py`, `tests/unit/test_engine_phase2.py`.

## 15. "How do I know the log was not edited afterwards?"

**Answer.** The audit repository is append-only and each record's hash covers
the canonical payload plus the previous hash, so a tampered payload, a deleted
record or a relinked chain is detected and `GET /executions/{id}/audit/verify`
names the first broken sequence number. Runs and approval decisions each write
their own verified chain, and the timeline joins steps with events.

**Evidence.** `src/storage/audit.py`, `tests/unit/test_audit_chain.py`,
`tests/integration/test_audit_api.py`. **Residual risk we name ourselves:** the
chain is not externally anchored, so an attacker who rewrites *everything*
including the head is out of scope (`docs/LIMITATIONS.md` §2).

## 16. "Can you reproduce a run that went wrong?"

**Answer.** Replay executes the immutable version row the original run pinned —
not "the file on disk" — under a new correlation id, refuses a tampered pin, and
reports normalized per-node match/missing/unexpected with the replay recorded on
the source run's chain. Backup/restore snapshots the database with a verified
manifest, and the migration upgrade path was tested from a 4-migration database
onwards.

**Evidence.** `src/app/replay.py`, `tests/acceptance/test_replay.py`,
`tests/integration/test_backup_restore.py`, `docs/DEPLOYMENT.md` §3.

---

## 17. "What did you measure, and where is the bottleneck?"

**Answer.** Measured, not asserted: worst API p95 **33.9–36.4 ms** across the
recorded runs (300 requests per endpoint, seeded with 200 runs), sandbox run
**≈39 ms** (`isolation=bwrap`, ~25 runs/s), shell worker tens of thousands of
events/s (the 200-event figure is timer-quantised — see `docs/PERF_REPORT.md`
§5). The bottleneck is not the API: it is the sandbox (a fresh interpreter per
run) and the SQLite single writer.

**Evidence.** `scripts/benchmark.py`, `docs/reports/perf.txt`,
`docs/PERF_REPORT.md`.

## 18. "How does this scale beyond one process?"

**Answer.** Workers scale horizontally today: they are independent consumer-group
members on `steps:<type>` streams, so N workers share the load and the
idempotency key keeps redelivery harmless. The API scales until the store does:
SQLite is a single writer, which is why the schema runner and repository layer
already accept DB-API connections and `asyncpg` is pinned — the PostgreSQL
migration is the documented next step, and the compose stack already runs the
PostgreSQL service.

**Evidence.** `src/bus/consumer.py`, `src/app/repositories.py`,
`src/app/db.py`, `docs/LIMITATIONS.md` §2.

## 19. "What breaks first under load, and what is the honest answer about it?"

**Answer.** Three things, in order: the rate limiter is per process, so N API
replicas allow N× the configured rate (fix: shared Redis bucket or an edge
limiter); the SQLite writer serialises concurrent writes (fix: the Postgres
migration); and a sandbox run is a fresh process, so untrusted execution is
CPU-bound (fix: run sandboxes on dedicated runners). All three are rows in
`docs/LIMITATIONS.md` with their compensation and their fix — the honest
answer is cheaper than pretending the platform is horizontally scalable today.

---

## Cross-questions in one line each

| Question | One-line answer |
|---|---|
| "Why not LangChain/Flowise?" | those are planner/UI conveniences; our contribution is the governance layer *around* planning — validation, policy, approvals, audit — which we would still need to write |
| "Why Redis Streams and not Kafka?" | one broker for a two-student deployment with consumer groups, DLQ and at-least-once semantics built in; the contract is frozen in `src/bus/events.py`, so the broker is replaceable |
| "Why not PostgreSQL from day one?" | SQLite made the build hermetic and the tests fast; the migration path is prepared (`asyncpg` pinned, repository layer DB-API agnostic), and the trade-off is documented rather than hidden |
| "What is the weakest part of the project?" | the usability evidence: the SUS protocol, instrument and a tested scorer exist, but the five sessions are pending — it is the first row of `docs/LIMITATIONS.md` §4 |
| "What would you do with two more weeks?" | run the SUS sessions, replace record-only `shell` with the sandboxed path, wire the benchmark into CI as a regression gate, and add a global audit anchor |
| "Is the AI platform in this repository?" | no: the LLM adapter is optional and off by default; the demo and CI never call a provider |
| "Can I run it?" | `pip install -r requirements*.txt`, `python -m pytest tests` (1055 passed, 2 skipped), `docker compose up --build`, or `bash scripts/export_reports.sh` to regenerate every number in this document |
