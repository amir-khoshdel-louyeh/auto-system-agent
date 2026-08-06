# Three-minute demo script (item [95])

**Purpose:** the recorded backup that plays if the live defense fails (no
broker, no Docker, no display, lecturer's machine). It is not a product tour:
it shows the three acceptance scenarios of the proposal (§12 W1/W2/W3), each
one backed by an artefact that exists in the repository.

**Status:** script and commands frozen 2026-10-18, every command in it was run
on the rehearsal host (`docs/reports/rehearsal.txt`). **The recording itself is
a human step and is still pending** — see the status note in
`docs/IMPLEMENTATION_STATUS.md` item [95].

## 0. Pre-flight (before recording, ~5 min)

```bash
# clean terminal, no services required for segments 1-3
cd <repo> && git switch --detach "$(git describe --tags --abbrev=0)"  # the tagged RC
pip install -r requirements.txt -r requirements-api.txt -r requirements-dev.txt
python -m pytest tests               # 1083 passed, 2 skipped  (baseline, optional)
```

- Terminal font: large enough to read at 720p, no personal shell prompt with
  paths, no other windows.
- Timer visible to *you*, not in frame. The segments below are the only ones
  that may not be cut.
- If a command is slow on the recording host, record the segments separately
  and cut the waiting time — the transcript files are the evidence, not the
  wall clock.

## 1. The timing sheet

| Time | Segment | On screen | Command (verbatim) |
|---|---|---|---|
| 0:00–0:20 | What this is | README title + one line: *the AI proposes, project-owned logic disposes, the repository remembers* | — |
| 0:20–1:00 | **W1** request → typed plan → DAG → policy → audit → replay | test transcript, then the generated traceability row | `python -m pytest tests/acceptance/test_planned_workspace.py -q` |
| 1:00–1:45 | **W2** versioned event-driven workflow with an approval and a forced retry | test transcript + audit chain | `python -m pytest tests/acceptance/test_replay.py tests/integration/test_bus_integration.py -q` |
| 1:45–2:25 | **W3** quarantine/reuse + a real sandbox escape attempt failing | test transcript | `python -m pytest tests/acceptance/test_memory_reuse_gate.py tests/acceptance/test_sandbox_isolation.py -q` |
| 2:25–2:45 | The evidence, not the promise | `docs/reports/` summary table, coverage + p95 lines | `cat docs/reports/export.txt` |
| 2:45–3:00 | Close | `docs/LIMITATIONS.md` §4 — the one area without measured evidence | — |

## 2. Segment by segment

### 0:00 — What it is (20 s, narration only)

> "A governed automation platform: a request or a YAML workflow becomes a
> validated DAG, every node passes a deterministic policy gate, risky nodes
> wait for a human quorum, commands execute inside a sandbox scoped to the run's
> workspace, work runs on Redis Streams, and every run is hash-chain audited and
> replayable. The LLM is an optional planner; it never authorises, executes or
> audits anything."

### 0:20 — W1 governed workspace onboarding (40 s)

Run:

```bash
python -m pytest tests/acceptance/test_planned_workspace.py -q
```

Show, in this order, as the test asserts them (`tests/acceptance/test_planned_workspace.py`):

1. the request `"create a workspace called demo-app with three folders and a README"`
   becomes a typed multi-step plan;
2. the plan compiles to a DAG accepted by the **same** validator a hand-written
   workflow uses;
3. the three folder nodes sit on one DAG level — they are dispatched
   concurrently, not in a chain;
4. every node has a `policy.decision` on the audit chain;
5. the run's step results are queryable;
6. the run **replays from its pinned version** and the normalized original and
   replay outputs match.

Narration: "the plan is not trusted input — it goes through the same loader,
validator and policy service as a YAML workflow. Each `shell` node then really
executes inside the sandbox, in that run's own workspace, and the replay proves
the run is reproducible."

### 1:00 — W2 versioned event-driven workflow (45 s)

Run:

```bash
python -m pytest tests/acceptance/test_replay.py tests/integration/test_bus_integration.py -q
```

Point at (`tests/acceptance/test_replay.py`, `tests/integration/test_bus_integration.py`):

- a cyclic YAML is rejected with `line:column`, so it is never persisted;
- the run pins `(workflow_id, version)` — the replay still executes that
  version after the file on disk is edited;
- a duplicate `file.arrived` event produces **no** duplicate side effect
  (idempotency key + persisted step state before the ack);
- the forced worker failure is visible as a retry and, past the budget, as a
  dead-letter entry;
- the approval decision and the audit history are both queryable, and the
  approval is recorded on the same chain as the run.

### 1:45 — W3 quarantine, reuse and the sandbox (40 s)

Run:

```bash
python -m pytest tests/acceptance/test_memory_reuse_gate.py tests/acceptance/test_sandbox_isolation.py -q
```

Three lines of narration, one per claim:

1. "a plan is stored **scrubbed** — the raw value never reaches the store, and a
   plan without consent is refused";
2. "a plan that failed stays quarantined and cannot be auto-reused; a plan that
   passed sandbox proof is reused, and `GET /memory/metrics` counts the planner
   calls that reuse avoided";
3. "untrusted plugin code is executed in a sandbox: it cannot climb out with
   `../`, cannot read `/etc/passwd`, cannot spawn a process, cannot open a
   socket, and the directory outside its workspace is byte-identical afterwards.
   Without OS isolation a `command` node is refused rather than run unguarded."

### 2:25 — The evidence (20 s)

```bash
cat docs/reports/export.txt      # one PASS/FAIL line per gate, 12 rows
```

Read the numbers, not the adjectives: **global coverage 81.35%**, **core gate
(`src/engine|safety|storage|bus`) 86.55%**, **worst API p95 31–36 ms vs the
300 ms NFR** (it moves a millisecond or two between runs; the transcript in
`docs/reports/perf.txt` is the authority), Bandit 0 at medium+, `pip-audit`
clean, secret scan clean, 1083 passed + 2 skipped. Then the honest one:

```bash
sed -n '/## 4. Testing gaps/,/^## 5/p' docs/LIMITATIONS.md
```

### 2:45 — Close (15 s)

> "Everything shown exists as a test, a script and a transcript in this
> repository. The one thing this project does not claim is the usability study
> — five sessions are still pending, and it is the first line of our limitations
> document."

## 3. If the live demo breaks (the actual backup plan)

| Failure during the defense | Fallback that keeps the claim intact |
|---|---|
| no Docker / broker down | play the video segments 1–3 (all hermetic, no services) |
| Redis available but flaky | `python -m pytest tests/integration/test_bus_integration.py -q` with `ASA_REDIS_*` unset — the contract tests skip explicitly instead of pretending |
| no display for the Tkinter client | show `tests/acceptance/test_workbench_walkthrough.py` (the four screens are driven against a live API) and note that widget-level automation is a documented gap |
| the rehearsal host is gone | `bash scripts/rehearse_demo.sh` regenerates the whole transcript, including the summary table |
| a number is challenged | `bash scripts/export_reports.sh` regenerates every artefact in `docs/reports/` from scratch |

## 4. What this script deliberately does **not** do

- It does not hide the sandbox's own limit: a `shell` command needs OS
  isolation (`bwrap` + user namespaces) and fails closed without it
  (`docs/LIMITATIONS.md` §1) — so the video's W1 segment is recorded on a host
  where `bwrap` works, and the transcript names the isolation layer used.
- It does not demo GUI automation: `gui_click`/`gui_type` are explicit
  non-production stubs.
- It does not claim the SUS result, the recording, or anything else a human has
  not produced yet.
