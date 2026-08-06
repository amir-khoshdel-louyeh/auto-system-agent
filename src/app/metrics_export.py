"""Post-RC item [100]: Prometheus text exposition of the platform's metrics.

The JSON endpoints answer *what happened* (`GET /metrics/executions`,
`GET /memory/metrics`); a scrape endpoint answers *is it healthy now* without a
bespoke client. This module is a pure function over the same aggregate rows
`storage.run_metrics.summarize` already computes, so the text a scraper reads
can be asserted exactly in a unit test — and no number is invented at render
time.

Format reference: Prometheus text exposition v0.0.4. Names are prefixed `asa_`
so they cannot collide with a shared exporter.
"""

from __future__ import annotations

from typing import Any

PREFIX = "asa"
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def escape_label(value: object) -> str:
    """Escape a label value per the exposition format (\\, ", newline)."""
    text = str(value)
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(labels: dict[str, Any] | None) -> str:
    if not labels:
        return ""
    parts = [f'{key}="{escape_label(value)}"' for key, value in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _sample(
    lines: list[str],
    name: str,
    value: float | int,
    *,
    help_text: str,
    metric_type: str = "gauge",
    labels: dict[str, Any] | None = None,
) -> None:
    """Emit one HELP/TYPE pair (once) and one sample line."""
    header = f"{PREFIX}_{name}"
    if f"# HELP {header} " not in "\n".join(lines):
        lines.append(f"# HELP {header} {help_text}")
        lines.append(f"# TYPE {header} {metric_type}")
    lines.append(f"{header}{_labels(labels)} {value}")


def render(
    metrics: dict[str, Any],
    *,
    memory: dict[str, Any] | None = None,
    build: dict[str, Any] | None = None,
) -> str:
    """Render run metrics (and optional memory/build metrics) as Prometheus text."""
    lines: list[str] = []
    _sample(lines, "runs_total", metrics.get("total", 0), help_text="Executions recorded")
    _sample(
        lines,
        "runs_succeeded_total",
        metrics.get("succeeded", 0),
        help_text="Executions that completed",
        metric_type="counter",
    )
    _sample(
        lines,
        "runs_failed_total",
        metrics.get("failed", 0),
        help_text="Executions that failed",
        metric_type="counter",
    )
    _sample(
        lines,
        "run_success_rate",
        metrics.get("success_rate", 0.0),
        help_text="Share of executions that completed (percent)",
    )
    _sample(
        lines,
        "run_latency_average_milliseconds",
        metrics.get("avg_latency_ms", 0.0),
        help_text="Average execution wall-clock time",
    )
    _sample(
        lines,
        "run_latency_p95_milliseconds",
        metrics.get("p95_latency_ms", 0.0),
        help_text="95th percentile execution wall-clock time",
    )
    _sample(
        lines,
        "step_retries_total",
        metrics.get("retries", 0),
        help_text="Step attempts beyond the first",
        metric_type="counter",
    )
    _sample(
        lines,
        "approval_requests_total",
        metrics.get("approval_count", 0),
        help_text="Approval requests that reached a decision",
        metric_type="counter",
    )
    _sample(
        lines,
        "approval_average_seconds",
        metrics.get("avg_approval_s", 0.0),
        help_text="Average time from approval request to first decision",
    )

    if memory:
        for state, count in sorted((memory.get("items_by_state") or {}).items()):
            _sample(
                lines,
                "memory_items",
                count,
                help_text="Memory items by lifecycle state",
                labels={"state": state},
            )
        _sample(
            lines,
            "memory_reuse_hits_total",
            memory.get("reuse_hits", 0),
            help_text="Requests answered from memory instead of planning",
            metric_type="counter",
        )
        _sample(
            lines,
            "memory_reuse_hit_rate",
            memory.get("hit_rate", 0.0),
            help_text="Share of memory consultations that were reuse hits",
        )

    if build:
        _sample(
            lines,
            "build_info",
            1,
            help_text="Build metadata (always 1)",
            labels={
                "version": build.get("version", "unknown"),
                "environment": build.get("environment", "unknown"),
            },
        )
    return "\n".join(lines) + "\n"


__all__ = ["CONTENT_TYPE", "PREFIX", "escape_label", "render"]
