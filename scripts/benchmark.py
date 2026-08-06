#!/usr/bin/env python3
"""Phase 9 item [86]: measure API latency percentiles and worker throughput.

Three measurements, all against the real code and printed with the machine
that produced them (no external LLM calls are involved, matching the NFR):

1. **API latency** — `GET /health` (no auth, no DB), `GET /executions` and
   `GET /executions/{id}/audit` (JWT + SQLite) driven over HTTP by a thread
   pool against a live uvicorn server with a seeded database. Reports
   p50/p95/p99/max and requests per second, and compares p95 with the NFR
   target of 300 ms.
2. **Shell worker throughput** — `step.dispatched` events drained by the real
   `shell` worker (policy gate + persist + verdict) over the in-memory bus,
   reported as events/second.
3. **Sandboxed plugin throughput** — the `custom` worker running a real
   plugin through the sandbox (bwrap + a fresh interpreter per run), which is
   the deliberately slow, isolated path.

Usage::

    python scripts/benchmark.py                      # defaults
    python scripts/benchmark.py --requests 500 --threads 16
    python scripts/benchmark.py --json docs/reports/perf.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

#: NFR target from the proposal (§7 Performance).
P95_TARGET_MS = 300.0

PASSWORD = "benchmark-admin-pw-123"
SECRET = "benchmark-secret-0123456789abcd"
PLUGIN_SOURCE = "def run(ctx):\n    open('out.txt', 'w').write('ok')\n    return {'ok': True}\n"


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile (no interpolation; small, deterministic)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered))) - 1))
    return ordered[index]


def latency_report(samples: list[float], *, wall_seconds: float) -> dict[str, float]:
    """Percentiles in milliseconds plus throughput for one endpoint."""
    return {
        "count": len(samples),
        "p50_ms": round(percentile(samples, 0.50) * 1000, 2),
        "p95_ms": round(percentile(samples, 0.95) * 1000, 2),
        "p99_ms": round(percentile(samples, 0.99) * 1000, 2),
        "max_ms": round(max(samples) * 1000, 2),
        "mean_ms": round(statistics.fmean(samples) * 1000, 2),
        "requests_per_second": round(len(samples) / wall_seconds, 1) if wall_seconds else 0.0,
    }


# -- API measurement -------------------------------------------------------
class ApiServer:
    """A live uvicorn app on an ephemeral port, seeded for real DB work."""

    def __init__(self, *, executions: int, audit_events: int) -> None:
        import uvicorn  # imported here so `--help` works without the API extras

        from app.db import init_db
        from app.main import create_app
        from app.models import ExecutionStore, WorkflowStore
        from app.settings import Settings
        from storage.audit import AuditStore, AuditTrail

        self._tmp = tempfile.TemporaryDirectory()
        path = str(Path(self._tmp.name) / "bench.db")
        self.settings = Settings(
            admin_user="admin",
            admin_password=PASSWORD,
            secret_key=SECRET,
            sqlite_path=path,
        )
        db = init_db(path)
        workflows = WorkflowStore(db)
        store = ExecutionStore(db)
        workflow_id = workflows.ensure_workflow("benchmark")
        self.execution_ids: list[str] = []
        for index in range(executions):
            run_id = f"bench-{index:016d}"
            store.start(workflow_id, 1, triggered_by="benchmark", run_id=run_id)
            store.record_step(run_id, "read", "SUCCEEDED")
            self.execution_ids.append(run_id)
        trail = AuditTrail(AuditStore(db), self.execution_ids[0])
        for index in range(audit_events):
            trail.record("bench.event", {"index": index})

        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_app(self.settings),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("API did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self._tmp.cleanup()


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(url: str, token: str = "") -> int:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - loopback
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _login(base_url: str) -> str:
    payload = json.dumps({"username": "admin", "password": PASSWORD}).encode()
    request = urllib.request.Request(
        f"{base_url}/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - loopback
        return str(json.loads(response.read())["access_token"])


def measure_endpoint(url: str, token: str, *, requests: int, threads: int) -> dict[str, float]:
    """Drive one endpoint with a thread pool; returns latency statistics."""
    samples: list[float] = []
    lock = threading.Lock()

    def worker(count: int) -> None:
        local: list[float] = []
        for _ in range(count):
            started = time.perf_counter()
            status = _get(url, token)
            elapsed = time.perf_counter() - started
            if status >= 400:
                raise RuntimeError(f"{url} answered {status}")
            local.append(elapsed)
        with lock:
            samples.extend(local)

    base, extra = divmod(requests, threads)
    counts = [base + 1] * extra + [base] * (threads - extra)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(worker, [count for count in counts if count]))
    wall = time.perf_counter() - started
    return latency_report(samples, wall_seconds=wall)


# -- worker measurement ----------------------------------------------------
def measure_shell_worker(events: int) -> dict[str, float]:
    """Events/second through the shell worker's **gate** path (item [99]).

    Since `shell` nodes execute for real in the sandbox, executing them here
    would measure the sandbox (see :func:`measure_shell_command`) rather than
    the bus. This measurement therefore injects a no-op execution function: it
    is the cost of gate → dispatch → durable verdict, with no process spawn,
    and the report says so.
    """
    from bus.backend import MemoryBackend
    from bus.events import new_event
    from bus.workers.core import RESULTS_STREAM, steps_stream
    from bus.workers.shell import make_shell_worker
    from engine.executor.gate import flatten_node

    async def no_exec(node_id: str, node: dict, ctx: dict) -> dict:
        return {"ok": node_id}

    async def scenario() -> float:
        backend = MemoryBackend()
        worker = make_shell_worker(backend, "bench-shell", exec_fn=no_exec)
        node = flatten_node({"type": "shell", "params": {"run": "ls -la"}})
        for index in range(events):
            await backend.publish(
                steps_stream("shell"),
                new_event(
                    "step.dispatched",
                    {"node_id": f"n{index}", "node": node},
                    correlation_id=f"run-{index}",
                    idempotency_key=f"run-{index}:n{index}",
                ),
            )
        started = time.perf_counter()
        await worker.drain_once()
        elapsed = time.perf_counter() - started
        results = await backend.read_range(RESULTS_STREAM)
        finished = [event for _, event in results if event.type == "step.finished"]
        if len(finished) != events:
            raise RuntimeError(f"expected {events} verdicts, saw {len(finished)}")
        return elapsed

    elapsed = asyncio.run(scenario())
    return {
        "count": events,
        "seconds": round(elapsed, 3),
        "events_per_second": round(events / elapsed, 1) if elapsed else 0.0,
        "measures": "gate + dispatch + verdict (no execution)",
    }


def measure_shell_command(runs: int) -> dict[str, Any]:
    """Real sandboxed `shell` runs/second through the shell worker (item [99])."""
    from bus.backend import MemoryBackend
    from bus.events import new_event
    from bus.workers.core import RESULTS_STREAM, steps_stream
    from bus.workers.shell import make_shell_worker

    workspace = Path(tempfile.mkdtemp(prefix="bench-shell-ws-"))

    async def scenario() -> tuple[float, str]:
        backend = MemoryBackend()
        worker = make_shell_worker(backend, "bench-shell-cmd")
        node = {"type": "shell", "params": {"run": "mkdir -p demo && printf ok > demo/f.txt"}}
        for index in range(runs):
            await backend.publish(
                steps_stream("shell"),
                new_event(
                    "step.dispatched",
                    {"node_id": f"s{index}", "node": node, "workspace": str(workspace)},
                    correlation_id=f"run-{index}",
                    idempotency_key=f"run-{index}:s{index}",
                ),
            )
        started = time.perf_counter()
        await worker.drain_once()
        elapsed = time.perf_counter() - started
        results = await backend.read_range(RESULTS_STREAM)
        finished = [event for _, event in results if event.type == "step.finished"]
        if len(finished) != runs:
            raise RuntimeError(f"expected {runs} shell verdicts, saw {len(finished)}")
        return elapsed, str(finished[0].data["output"].get("mode", "unknown"))

    try:
        elapsed, mode = asyncio.run(scenario())
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return {
        "count": runs,
        "seconds": round(elapsed, 3),
        "runs_per_second": round(runs / elapsed, 2) if elapsed else 0.0,
        "mean_ms_per_run": round(elapsed / runs * 1000, 1) if runs else 0.0,
        "mode": mode,
    }


def measure_sandbox_worker(runs: int) -> dict[str, Any]:
    """Sandboxed plugin runs/second through the real `custom` worker."""
    from bus.backend import MemoryBackend
    from bus.events import new_event
    from bus.workers.core import RESULTS_STREAM, steps_stream
    from bus.workers.custom import make_custom_worker
    from sandbox.plugins import PluginRegistry

    manifest = {
        "name": "bench-plugin",
        "version": "1.0.0",
        "entrypoint": "plugin.py",
        "inputs": {},
        "outputs": {"ok": "bool"},
    }
    registry = PluginRegistry()
    registry.register(manifest, PLUGIN_SOURCE)

    async def scenario() -> tuple[float, str]:
        backend = MemoryBackend()
        worker = make_custom_worker(backend, "bench-custom", registry)
        node = {"type": "custom", "params": {"func": "bench-plugin"}}
        for index in range(runs):
            await backend.publish(
                steps_stream("custom"),
                new_event(
                    "step.dispatched",
                    {"node_id": f"c{index}", "node": node},
                    correlation_id=f"run-{index}",
                    idempotency_key=f"run-{index}:c{index}",
                ),
            )
        started = time.perf_counter()
        await worker.drain_once()
        elapsed = time.perf_counter() - started
        results = await backend.read_range(RESULTS_STREAM)
        finished = [event for _, event in results if event.type == "step.finished"]
        if len(finished) != runs:
            raise RuntimeError(f"expected {runs} sandbox verdicts, saw {len(finished)}")
        isolation = str(finished[0].data["output"].get("isolation", "unknown"))
        return elapsed, isolation

    elapsed, isolation = asyncio.run(scenario())
    return {
        "count": runs,
        "seconds": round(elapsed, 3),
        "runs_per_second": round(runs / elapsed, 2) if elapsed else 0.0,
        "mean_ms_per_run": round(elapsed / runs * 1000, 1) if runs else 0.0,
        "isolation": isolation,
    }


def environment() -> dict[str, Any]:
    """Enough context to read the numbers (they are machine-specific)."""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from sandbox.runner import bwrap_available

    server = ApiServer(executions=args.seed_executions, audit_events=args.audit_events)
    try:
        server.start()
        token = _login(server.base_url)
        execution_id = server.execution_ids[0]
        api = {
            "health": measure_endpoint(
                f"{server.base_url}/health", "", requests=args.requests, threads=args.threads
            ),
            "executions": measure_endpoint(
                f"{server.base_url}/executions",
                token,
                requests=args.requests,
                threads=args.threads,
            ),
            "audit_page": measure_endpoint(
                f"{server.base_url}/executions/{execution_id}/audit",
                token,
                requests=args.requests,
                threads=args.threads,
            ),
        }
    finally:
        server.stop()

    workers = (
        {}
        if getattr(args, "api_only", False)
        else {
            "shell_steps": measure_shell_worker(args.worker_events),
            "shell_commands": measure_shell_command(args.shell_runs),
            "sandbox_runs": measure_sandbox_worker(args.sandbox_runs),
        }
    )
    worst_p95 = max(report["p95_ms"] for report in api.values())
    budget_ms = float(getattr(args, "max_p95", P95_TARGET_MS))
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {**environment(), "bwrap": bwrap_available()},
        "parameters": {
            "requests_per_endpoint": args.requests,
            "threads": args.threads,
            "seeded_executions": args.seed_executions,
            "audit_events": args.audit_events,
            "worker_events": args.worker_events,
            "shell_runs": args.shell_runs,
            "sandbox_runs": args.sandbox_runs,
        },
        "api": api,
        "workers": workers,
        "nfr": {
            "p95_target_ms": budget_ms,
            "worst_p95_ms": worst_p95,
            "meets_target": worst_p95 < budget_ms,
        },
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"benchmark {report['timestamp']}",
        f"  python {report['environment']['python']} on {report['environment']['machine']}"
        f" ({report['environment']['cpu_count']} cpus, bwrap={report['environment']['bwrap']})",
        "",
        "API (milliseconds, lower is better):",
        "  endpoint        count  p50     p95     p99     max     mean    req/s",
    ]
    for name, stats in report["api"].items():
        lines.append(
            f"  {name:<15} {stats['count']:>5}  {stats['p50_ms']:>6}  {stats['p95_ms']:>6}"
            f"  {stats['p99_ms']:>6}  {stats['max_ms']:>6}  {stats['mean_ms']:>6}"
            f"  {stats['requests_per_second']:>6}"
        )
    nfr = report["nfr"]
    lines += [
        "",
        f"  worst p95 = {nfr['worst_p95_ms']} ms vs target {nfr['p95_target_ms']} ms"
        f" -> {'PASS' if nfr['meets_target'] else 'FAIL'}",
        "",
        "Workers:",
    ]
    if not report["workers"]:
        lines.append("  (skipped: --api-only; the CI regression gate measures HTTP latency only)")
        return "\n".join(lines)
    shell = report["workers"]["shell_steps"]
    commands = report["workers"]["shell_commands"]
    sandbox = report["workers"]["sandbox_runs"]
    lines.append(
        f"  shell gate path   {shell['count']} events in {shell['seconds']}s"
        f" = {shell['events_per_second']} events/s ({shell['measures']})"
    )
    lines.append(
        f"  shell command     {commands['count']} runs in {commands['seconds']}s"
        f" = {commands['runs_per_second']} runs/s ({commands['mean_ms_per_run']} ms/run,"
        f" mode={commands['mode']})"
    )
    lines.append(
        f"  sandbox worker    {sandbox['count']} runs in {sandbox['seconds']}s"
        f" = {sandbox['runs_per_second']} runs/s ({sandbox['mean_ms_per_run']} ms/run,"
        f" isolation={sandbox['isolation']})"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="API latency and worker throughput benchmark")
    parser.add_argument("--requests", type=int, default=300, help="requests per endpoint")
    parser.add_argument("--threads", type=int, default=8, help="client threads")
    parser.add_argument("--seed-executions", type=int, default=200, help="rows in the run list")
    parser.add_argument("--audit-events", type=int, default=50, help="events on the audited run")
    parser.add_argument("--worker-events", type=int, default=200, help="shell step events")
    parser.add_argument("--shell-runs", type=int, default=10, help="real sandboxed shell runs")
    parser.add_argument("--sandbox-runs", type=int, default=20, help="sandboxed plugin runs")
    parser.add_argument("--json", default="", help="also write the raw result here")
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="skip worker measurements (the CI regression gate: HTTP latency only)",
    )
    parser.add_argument(
        "--max-p95",
        type=float,
        default=P95_TARGET_MS,
        help=f"p95 budget in milliseconds (default {P95_TARGET_MS:g}, proposal NFR)",
    )
    args = parser.parse_args(argv)

    report = run_benchmark(args)
    print(render(report))
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nraw result written to {target}")
    return 0 if report["nfr"]["meets_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
