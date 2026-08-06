# Performance report

**Item:** Phase 9 — [86] Benchmark API p95 and worker throughput
**Measured:** 2026-10-18, `local_commit`, run `python scripts/benchmark.py`
**Machine:** x86_64, 24 CPUs, Python 3.14.8, `bwrap` available
**Raw output:** `docs/reports/perf.txt` (console) and `docs/reports/perf.json`

The command is the source of truth; the tables below are its output copied
verbatim. No external LLM call is involved, matching the NFR wording
("excluding external LLM calls").

```bash
python scripts/benchmark.py --requests 300 --threads 8 \
    --seed-executions 200 --audit-events 50 \
    --worker-events 200 --sandbox-runs 20 --json docs/reports/perf.json
```

## 1. API latency (live uvicorn, JWT, SQLite with 200 seeded runs)

| Endpoint | Requests | p50 | **p95** | p99 | max | mean | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| `GET /health` (no auth, no DB) | 300 | 4.20 ms | **5.92 ms** | 7.13 ms | 7.88 ms | 4.19 ms | 1 862 |
| `GET /executions` (JWT + DB, 200 rows) | 300 | 22.36 ms | **33.92 ms** | 44.00 ms | 60.32 ms | 23.30 ms | 336 |
| `GET /executions/{id}/audit` (JWT + 50-event chain) | 300 | 18.15 ms | **29.46 ms** | 58.35 ms | 81.21 ms | 19.82 ms | 395 |

**NFR target: API p95 < 300 ms — PASS with an order of magnitude of headroom**
(worst p95 = 33.92 ms; the audit page is the heaviest read at 29.46 ms).

Reading the numbers:

- The unauthenticated health probe (5.92 ms p95) is the substrate: process,
  JSON encoding, TLS-less loopback round trip.
- The authenticated reads add JWT verification (PBKDF2 is *not* in this path)
  and SQLite queries; `/executions` also resolves the workflow name per row.
- Latency is dominated by the test client's connection handling (a new TCP
  connection per request, like the Tkinter client — see limitations).

## 2. Worker throughput

Three measurements, because they answer three different questions (the middle
one is new in item [99] — before it, `shell` nodes did not execute at all):

| What | Work | Result |
|---|---|---|
| Gate/dispatch path (`shell` worker with execution stubbed: policy re-check + durable verdict over the in-memory bus) | 200 `step.dispatched` events | 200 verdicts in 0.002 s = **128 883 events/s** |
| **Real sandboxed `shell` command** (item [99]: bwrap + `/bin/sh` + file diff) | 20 runs | **≈77 runs/s** (13.0 ms/run, mode `sandboxed (bwrap)`) |
| Sandboxed **plugin** (`custom` worker: bwrap + fresh Python interpreter + file diff) | 20 runs | **26.27 runs/s** (38.1 ms/run, isolation `bwrap`) |

The first row is not execution: it isolates the bus, the gate and the durable
verdict from the cost of running anything, and after item [99] it is measured
with execution stubbed (see `scripts/benchmark.py::measure_shell_worker`), so it
must never be quoted as shell-node throughput. The other two rows are the real
cost of isolation — and the reason a command is ~3× cheaper than a plugin: a
shell command needs no fresh interpreter, only the namespace setup and the
process.

For planning: a workflow with 20 sandboxed `shell` commands takes roughly 0.26 s
of worker time (20 sandboxed plugins ≈ 0.76 s) when run sequentially, and
node-level parallelism in a DAG level divides that by the worker count.

## 3. What these numbers are not

- **Not a load test.** `--requests 300 --threads 8` is a repeatable smoke
  measurement on one machine, not a capacity plan; there is no soak, no
  long-running memory profile, and no saturation curve.
- **No connection reuse.** Each request opens a new TCP connection (stdlib
  `urllib`, the same client shape the Tkinter workbench uses). A keep-alive
  HTTP client would show lower p95/p99 and higher req/s.
- **Loopback only.** TLS, a reverse proxy and the network are excluded.
- **Redis is not in the path.** The worker measurement uses the in-memory
  backend, so it isolates worker logic from broker latency. Real Redis
  Streams add a round trip per event; the CI-gated Redis tests cover
  correctness, not throughput.
- **Machine-specific.** Re-run the command on the target host before quoting
  a number in the defense; the machine line is printed with every run.

## 4. Reproducing

```bash
python scripts/benchmark.py                     # defaults: 300 requests / endpoint
python scripts/benchmark.py --requests 500 --threads 16 --sandbox-runs 50
```

Exit status is 0 when the p95 target is met and 1 otherwise, so the command
can be wired into a regression gate. `docs/reports/perf.json` holds the raw
sample (parameters, environment, percentiles, worker rates) for the SE
package (item [93]).

## 5. Re-measurement at the release candidate (Phase 10, [93])

The Phase 9 numbers above are the run this report was written from. The
release-candidate export re-ran the same command and produced
`docs/reports/perf.txt` (the artefact the release notes cite):

| Endpoint | p50 | **p95** | p99 | req/s |
|---|---:|---:|---:|---:|
| `GET /health` | 4.08 ms | **6.27 ms** | 6.95 ms | 1 878 |
| `GET /executions` | 22.36 ms | **36.44 ms** | 42.35 ms | 334 |
| `GET /executions/{id}/audit` | 19.61 ms | **29.76 ms** | 55.37 ms | 379 |

Worst p95 = **36.44 ms** in that run; the latest export (`docs/reports/perf.txt`,
which is what the release notes cite) measured **31.48 ms**. Workers at that
export: gate path **134 956 events/s** (execution stubbed), a real sandboxed
`shell` command **13.0 ms/run** (77.1 runs/s), a sandboxed plugin **39.0 ms/run**
(25.7 runs/s). `docs/reports/perf.txt` is re-written by every export, so the
release notes quote a range rather than one run.

Two honest notes about variance, because a defence should expect the question:

- **p95 moves by a few milliseconds between runs**: 33.92 → 35.72 → 36.44 →
  34.50 → 31.48 ms on the same machine, all an order of magnitude under the NFR.
  The conclusion does not depend on which run is quoted, but a single number read
  out loud should be presented as "about 31–36 ms", not as a constant.
- **The gate-path figure is timer-quantised**, not a throughput trend: 200 events
  complete in 0.001–0.004 s, so the reported rate swings between ~50 k and
  ~135 k events/s. Quote it as "tens of thousands of events per second" of
  gate/dispatch/persist overhead, never as shell-node throughput.
- Sandbox cost is stable and cheap for commands: **38–40 ms/run** for plugins,
  **13–14 ms/run** for shell commands (item [99]).
