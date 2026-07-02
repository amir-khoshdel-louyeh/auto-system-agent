"""A3 system metrics: stdlib psutil-equivalent sampling (P4.3).

CPU from /proc/stat deltas, RAM from /proc/meminfo, disk from
shutil.disk_usage; queue depth is supplied by the caller (executor).
Formulae stay pure so tests need no live system.
"""

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Metric:
    """One sample: timestamp plus cpu/mem/disk percent and queue depth."""

    ts: float = 0.0
    cpu: float = 0.0
    mem: float = 0.0
    disk: float = 0.0
    queue: int = 0


def read_cpu_times() -> tuple[int, int]:
    """Return (idle, total) jiffies from /proc/stat, or (0, 0) off-Linux."""
    try:
        first = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    except (OSError, IndexError):
        return (0, 0)
    try:
        numbers = [int(part) for part in first[1:]]
    except ValueError:
        return (0, 0)
    if not numbers:
        return (0, 0)
    idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
    return (idle, sum(numbers))


def cpu_percent_between(before: tuple[int, int], after: tuple[int, int]) -> float:
    """CPU busy percent between two read_cpu_times() snapshots."""
    idle_delta = after[0] - before[0]
    total_delta = after[1] - before[1]
    if total_delta <= 0 or idle_delta < 0:
        return 0.0
    return round(max(0.0, min(100.0, (total_delta - idle_delta) / total_delta * 100.0)), 1)


def read_mem_percent() -> float:
    """RAM used percent from /proc/meminfo, or 0.0 when unavailable."""
    total = available = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable"):
                available = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return 0.0
    if not total:
        return 0.0
    return round(max(0.0, min(100.0, (total - (available or 0)) / total * 100.0)), 1)


def read_disk_percent(path: str | Path = "/") -> float:
    """Disk used percent for a path, or 0.0 when unreadable."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return 0.0
    if not usage.total:
        return 0.0
    return round(usage.used / usage.total * 100.0, 1)


def avg_cpu(metrics: list[Metric], window_seconds: float = 60.0) -> float:
    """Mean cpu over samples inside the trailing window (0.0 when empty)."""
    if not metrics or window_seconds <= 0:
        return 0.0
    cutoff = metrics[-1].ts - window_seconds
    values = [metric.cpu for metric in metrics if metric.ts >= cutoff]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 1)


def success_rate(succeeded: int, total: int) -> float:
    """ok/total as a 0-100 percent (100.0 when nothing ran)."""
    if total <= 0:
        return 100.0
    return round(max(0.0, min(100.0, succeeded / total * 100.0)), 1)


def mttr(outages: list[tuple[float, float]]) -> float:
    """Mean time to recovery in seconds over (failed_at, recovered_at) pairs."""
    durations = [max(0.0, end - start) for start, end in outages if end >= start]
    if not durations:
        return 0.0
    return round(sum(durations) / len(durations), 1)


def throughput(count: int, window_seconds: float) -> float:
    """Events per second over a window (0.0 for empty windows)."""
    if window_seconds <= 0 or count <= 0:
        return 0.0
    return round(count / window_seconds, 2)


@dataclass
class MetricSampler:
    """Stateful sampler: cpu deltas need the previous snapshot."""

    disk_path: str | Path = "/"
    _last_cpu: tuple[int, int] | None = field(default=None, repr=False)

    def sample(self, queue_depth: int = 0, now: float | None = None) -> Metric:
        current = read_cpu_times()
        if self._last_cpu is None:
            cpu = 0.0
        else:
            cpu = cpu_percent_between(self._last_cpu, current)
        self._last_cpu = current
        return Metric(
            ts=now if now is not None else time.time(),
            cpu=cpu,
            mem=read_mem_percent(),
            disk=read_disk_percent(self.disk_path),
            queue=max(0, int(queue_depth)),
        )
