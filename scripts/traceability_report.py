#!/usr/bin/env python3
"""Phase 10 item [91]: requirements-to-test traceability, machine-checked.

`docs/TRACEABILITY.md` is written by hand, and a hand-written matrix rots: a
test file gets renamed and the row keeps claiming coverage. This script holds
the mapping as data and *checks* it — every referenced module and test file
must exist, every referenced test file must contribute at least one collected
test, and all sixteen functional requirements plus the non-functional areas
must be mapped. Then it renders `docs/reports/traceability.md` with the real
collected counts.

Usage::

    python scripts/traceability_report.py                  # write the report
    python scripts/traceability_report.py --check          # validate only
    python scripts/traceability_report.py --out docs/reports/traceability.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Frozen requirement vocabulary (proposal §6). A missing id is a defect.
FR_IDS: tuple[str, ...] = tuple(f"FR-{index:02d}" for index in range(1, 17))

#: Non-functional areas from proposal §7.
NFR_IDS: tuple[str, ...] = (
    "Performance",
    "Reliability",
    "Maintainability",
    "Testability",
    "Security",
    "Privacy",
    "Usability",
    "Deployability",
    "Recovery",
)


@dataclass(frozen=True)
class Requirement:
    """One requirement, where it lives and what proves it."""

    id: str
    title: str
    modules: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    note: str = ""
    pending: bool = False


@dataclass
class Report:
    """The rendered mapping plus what validation found."""

    requirements: tuple[Requirement, ...]
    counts: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


FUNCTIONAL: tuple[Requirement, ...] = (
    Requirement(
        "FR-01",
        "Password hashing and short-lived JWT, no plaintext secrets",
        ("src/app/auth.py", "src/app/security.py", "src/app/db.py"),
        ("tests/integration/test_app_auth.py", "tests/unit/test_app_security.py"),
    ),
    Requirement(
        "FR-02",
        "RBAC enforced at the API layer and in the services",
        ("src/app/deps.py", "src/app/roles.py", "src/safety/policy_service.py"),
        ("tests/integration/test_app_rbac.py", "tests/unit/test_policy_context.py"),
    ),
    Requirement(
        "FR-03",
        "Typed YAML DSL with name, version, triggers, nodes, edges, policy",
        ("src/engine/dsl/loader.py", "src/engine/dsl/compiler.py"),
        ("tests/unit/test_engine_phase1.py", "tests/integration/test_workflow_store.py"),
    ),
    Requirement(
        "FR-04",
        "Full validation with precise line/column findings",
        ("src/engine/validator/validator.py", "src/app/workflows_api.py"),
        (
            "tests/integration/test_workflows_validate.py",
            "tests/unit/test_engine_phase1.py",
            # Item [104]: the publish route reuses the frozen finding shape.
            "tests/integration/test_execution_api.py",
        ),
    ),
    Requirement(
        "FR-05",
        "Immutable versions; a run pins (workflow_id, version)",
        ("src/app/models.py", "src/app/run_service.py"),
        (
            "tests/integration/test_workflow_store.py",
            "tests/integration/test_execution_store.py",
            # Items [104]/[106]: publish, start and poll one execution row.
            "tests/integration/test_execution_api.py",
            "tests/acceptance/test_workbench_walkthrough.py",
        ),
    ),
    Requirement(
        "FR-06",
        "Offline planner into a typed plan through the shared validator/policy",
        ("src/orchestration/offline_planner.py", "src/orchestration/plan_compiler.py"),
        (
            "tests/unit/test_planner_rules.py",
            "tests/unit/test_plan_compiler.py",
            "tests/acceptance/test_planned_workspace.py",
        ),
    ),
    Requirement(
        "FR-07",
        "Policy returns exactly ALLOW / NEED_APPROVAL / DENY with score and reasons",
        (
            "src/safety/policy_service.py",
            "src/safety/explanations.py",
            "src/safety/dryrun.py",
            "src/app/policy_api.py",
        ),
        (
            "tests/unit/test_policy_service.py",
            "tests/unit/test_policy_adversarial.py",
            "tests/unit/test_policy_explanations.py",
            "tests/integration/test_dryrun_api.py",
        ),
    ),
    Requirement(
        "FR-08",
        "Approval request, quorum, expiry, final decision",
        ("src/app/approvals.py", "src/app/approvals_api.py"),
        ("tests/integration/test_approvals.py", "tests/integration/test_approvals_api.py"),
    ),
    Requirement(
        "FR-09",
        "Typed events with correlation and idempotency keys; duplicates safe",
        ("src/bus/events.py", "src/bus/triggers.py"),
        (
            "tests/unit/test_bus_events.py",
            "tests/unit/test_bus_idempotency.py",
            "tests/integration/test_bus_integration.py",
        ),
    ),
    Requirement(
        "FR-10",
        "DAG scheduling with concurrency, propagation, timeout, retry, compensation",
        (
            "src/engine/executor/scheduler.py",
            "src/engine/executor/compensate.py",
            "src/bus/workers/dispatcher.py",
        ),
        (
            "tests/unit/test_engine_phase2.py",
            "tests/unit/test_engine_concurrency.py",
            "tests/unit/test_engine_retry.py",
            # Item [99]: shell nodes execute for real, in the run's workspace.
            "tests/integration/test_shell_execution.py",
        ),
    ),
    Requirement(
        "FR-11",
        "Typed worker results; no raw secrets or unbounded output persisted",
        ("src/bus/workers/core.py", "src/bus/workers/shell.py", "src/app/models.py"),
        (
            "tests/unit/test_bus_workers.py",
            "tests/unit/test_bus_persist.py",
            "tests/unit/test_sandbox_commands.py",
        ),
    ),
    Requirement(
        "FR-12",
        "Append-only hash-chain audit with tamper detection",
        ("src/storage/audit.py", "src/app/audit_api.py"),
        (
            "tests/unit/test_audit_chain.py",
            "tests/unit/test_audit_trail.py",
            "tests/integration/test_audit_api.py",
            "tests/integration/test_audit_run_path.py",
        ),
    ),
    Requirement(
        "FR-13",
        "Replay from the pinned version with normalized output comparison",
        ("src/app/replay.py", "src/app/audit_api.py"),
        (
            "tests/integration/test_replay.py",
            "tests/acceptance/test_replay.py",
            # Item [104]: POST /executions/{id}/replay is the client-visible path.
            "tests/integration/test_execution_api.py",
        ),
    ),
    Requirement(
        "FR-14",
        "Consent-gated, PII-scrubbed shared memory with filtered retrieval",
        ("src/memory/store.py", "src/memory/retrieval.py", "src/app/memory_api.py"),
        (
            "tests/unit/test_memory_scrub.py",
            "tests/unit/test_memory_retrieval.py",
            "tests/integration/test_memory_api.py",
        ),
    ),
    Requirement(
        "FR-15",
        "Reputation and quarantine gate reuse",
        ("src/memory/reputation.py", "src/memory/states.py"),
        (
            "tests/unit/test_memory_reputation.py",
            "tests/acceptance/test_memory_reuse_gate.py",
        ),
    ),
    Requirement(
        "FR-16",
        "Structured logs and metrics for runs, retries, approvals, replay, memory",
        ("src/storage/metrics.py", "src/storage/run_metrics.py", "src/app/logging.py"),
        (
            "tests/unit/test_run_metrics.py",
            "tests/unit/test_log_redaction.py",
            "tests/integration/test_audit_api.py",
        ),
    ),
)

NON_FUNCTIONAL: tuple[Requirement, ...] = (
    Requirement(
        "Performance",
        "API p95 < 300 ms excluding LLM calls",
        ("scripts/benchmark.py",),
        (),
        note="measured worst p95 33.92 ms — `docs/PERF_REPORT.md`",
    ),
    Requirement(
        "Reliability",
        "At-least-once delivery, idempotent workers, no duplicate side effect",
        ("src/bus/consumer.py", "src/bus/workers/core.py"),
        ("tests/unit/test_bus_idempotency.py", "tests/integration/test_bus_integration.py"),
    ),
    Requirement(
        "Maintainability",
        "Type hints, focused modules, lint and type gates",
        ("pyproject.toml", ".github/workflows/ci.yml"),
        (),
        note="`ruff check .`, `ruff format --check .`, `mypy src` in CI",
    ),
    Requirement(
        "Testability",
        "Coverage floor on the critical packages",
        ("pyproject.toml",),
        (),
        note="core `engine|safety|storage|bus` 86.1%, global 80.75%, floor 78%",
    ),
    Requirement(
        "Security",
        "No unapproved command; static analysis and dependency audit clean",
        ("docs/SECURITY_REPORT.md", "docs/THREAT_MODEL.md", "scripts/secret_scan.py"),
        (
            "tests/unit/test_command_guard.py",
            "tests/unit/test_secret_scan.py",
            "tests/acceptance/test_sandbox_isolation.py",
            # Item [99]: a shell command is refused when it cannot be isolated.
            "tests/unit/test_sandbox_commands.py",
        ),
    ),
    Requirement(
        "Privacy",
        "Consent, PII scrubbing and erasure",
        ("src/memory/scrub.py", "src/memory/consent.py"),
        (
            "tests/unit/test_memory_scrub.py",
            "tests/integration/test_memory_api.py",
        ),
    ),
    Requirement(
        "Usability",
        "A first-time user completes the five client tasks; SUS recorded",
        (
            "docs/USABILITY_STUDY.md",
            "scripts/sus_score.py",
            "src/launcher.py",
            # Item [110]: the five-screen shell and its visual language.
            "src/ui/workbench.py",
            "src/ui/theme.py",
        ),
        (
            "tests/acceptance/test_workbench_walkthrough.py",
            "tests/unit/test_sus_score.py",
            # Item [105]: one command starts the API and the workbench.
            "tests/integration/test_launcher.py",
            # Item [110]: the real window, where a display exists.
            "tests/acceptance/test_workbench_window.py",
        ),
        note="protocol and scorer ready — five sessions not yet run",
        pending=True,
    ),
    Requirement(
        "Deployability",
        "One compose file that works from a clone; production refuses dev secrets",
        ("compose.yml", "compose.prod.yml", "docs/DEPLOYMENT.md"),
        ("tests/integration/test_production_config.py",),
    ),
    Requirement(
        "Recovery",
        "Crash/retry/DLQ observable; backup/restore and upgrade proven",
        ("scripts/backup.py", "src/engine/executor/states.py"),
        (
            "tests/integration/test_backup_restore.py",
            "tests/unit/test_engine_states.py",
            "tests/unit/test_bus_retry.py",
        ),
        note="no full chaos suite (see `docs/LIMITATIONS.md`)",
    ),
)

ALL: tuple[Requirement, ...] = FUNCTIONAL + NON_FUNCTIONAL


def validate(requirements: tuple[Requirement, ...] = ALL, *, root: Path = ROOT) -> list[str]:
    """Every cited path and test file must exist, and every id must be mapped."""
    problems: list[str] = []
    by_id = {requirement.id: requirement for requirement in requirements}
    if len(by_id) != len(requirements):
        problems.append("duplicate requirement id in the mapping")
    for requirement_id in FR_IDS:
        if requirement_id not in by_id:
            problems.append(f"{requirement_id} is not mapped to anything")
    for area in NFR_IDS:
        if area not in by_id:
            problems.append(f"NFR area {area!r} is not mapped to anything")

    for requirement in requirements:
        if not requirement.modules and not requirement.tests and not requirement.note:
            problems.append(f"{requirement.id}: no module, test or note")
        for module in requirement.modules:
            if not (root / module).exists():
                problems.append(f"{requirement.id}: missing module {module}")
        for test in requirement.tests:
            path = root / test
            if not path.exists():
                problems.append(f"{requirement.id}: missing test file {test}")
                continue
            if "def test" not in path.read_text(encoding="utf-8"):
                problems.append(f"{requirement.id}: {test} contains no test function")
    return problems


def collected_counts(files: list[str], *, root: Path = ROOT) -> tuple[dict[str, int], str]:
    """Collected test counts per file, from one pytest --collect-only run."""
    if not files:
        return {}, ""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *files],
        cwd=root,
        capture_output=True,
        text=True,
    )
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        # pytest <9 lists `path::test_name`; pytest 9 -q summarises `path: N`.
        summary = re.match(r"^(?P<path>\S+\.py):\s*(?P<count>\d+)$", line.strip())
        if summary:
            counts[summary.group("path")] = int(summary.group("count"))
            continue
        if "::" in line and line.strip().endswith(tuple("abcdefghijklmnopqrstuvwxyz0123456789_")):
            path = line.split("::", 1)[0].strip()
            counts[path] = counts.get(path, 0) + 1
    if result.returncode != 0:
        return counts, f"pytest --collect-only failed: {result.stdout.strip()[-400:]}"
    return counts, ""


def missing_collections(requirements: tuple[Requirement, ...], counts: dict[str, int]) -> list[str]:
    """Test files that contributed no collected test."""
    problems: list[str] = []
    for requirement in requirements:
        for test in requirement.tests:
            if counts.get(test, 0) == 0:
                problems.append(f"{requirement.id}: {test} collected no tests")
    return problems


def render(report: Report) -> str:
    """The markdown report (regenerated, never hand-edited)."""
    counts = report.counts
    total_files = len({test for requirement in report.requirements for test in requirement.tests})
    total_tests = sum(
        counts.get(test, 0) for requirement in report.requirements for test in requirement.tests
    )
    lines = [
        "# Requirements-to-test traceability (generated)",
        "",
        "**Item:** Phase 10 — [91] Complete requirements-to-test traceability  ",
        f"**Generated:** {datetime.now(tz=timezone.utc).isoformat(timespec='seconds')} "
        "by `scripts/traceability_report.py`  ",
        f"**Scope:** {len(report.requirements)} requirements, {total_files} test files, "
        f"{total_tests} collected tests",
        "",
        "Regenerate with `python scripts/traceability_report.py`; validate without "
        "writing with `--check`. A hand-edited copy of this file is a claim, not "
        "evidence — the script asserts every module and test file exists and "
        "collects at least one test.",
        "",
        "## 1. Functional requirements (proposal §6)",
        "",
        "| ID | Requirement | Implementation | Test evidence | Collected |",
        "|---|---|---|---|---:|",
    ]
    for requirement in FUNCTIONAL:
        count = sum(counts.get(test, 0) for test in requirement.tests)
        modules = ", ".join(f"`{module}`" for module in requirement.modules) or "—"
        tests = ", ".join(f"`{test}`" for test in requirement.tests) or "—"
        lines.append(f"| {requirement.id} | {requirement.title} | {modules} | {tests} | {count} |")

    lines += [
        "",
        "## 2. Non-functional areas (proposal §7)",
        "",
        "| Area | Target | Evidence | Tests | State |",
        "|---|---|---|---:|---|",
    ]
    for requirement in NON_FUNCTIONAL:
        count = sum(counts.get(test, 0) for test in requirement.tests)
        evidence = ", ".join(f"`{module}`" for module in requirement.modules) or "—"
        if requirement.note:
            evidence += f" — {requirement.note}"
        state = "PENDING" if requirement.pending else "proven"
        lines.append(f"| {requirement.id} | {requirement.title} | {evidence} | {count} | {state} |")

    pending = [requirement.id for requirement in report.requirements if requirement.pending]
    lines += [
        "",
        "## 3. Honest gaps",
        "",
        "| Gap | Where it is recorded |",
        "|---|---|",
        f"| Usability study not run ({', '.join(pending) or 'none'}) | `docs/USABILITY_STUDY.md` §6 |",
        "| No dynamic/penetration testing; static analysis only | `docs/SECURITY_REPORT.md` §4 |",
        "| No full chaos suite (kill-mid-run covered) | `docs/LIMITATIONS.md` |",
        "| Deployment verified on one host, not a second machine | `docs/DEPLOYMENT.md` §5 |",
        "| PostgreSQL is in the topology but not the application store | `docs/LIMITATIONS.md` |",
        "",
        "## 4. Validation",
        "",
    ]
    if report.problems:
        lines.append("**Problems found — the mapping is not clean:**")
        lines += [f"- {problem}" for problem in report.problems]
    else:
        lines.append(
            "All cited modules and test files exist, every referenced test file "
            "collects at least one test, and every `FR-01…FR-16` id plus all nine "
            "non-functional areas are mapped."
        )
    return "\n".join(lines) + "\n"


def build(*, root: Path = ROOT, with_counts: bool = True) -> Report:
    """Validate and (optionally) collect counts, ready for rendering."""
    problems = validate(ALL, root=root)
    counts: dict[str, int] = {}
    if with_counts:
        files = sorted({test for requirement in ALL for test in requirement.tests})
        counts, collection_error = collected_counts(files, root=root)
        if collection_error:
            problems.append(collection_error)
        problems.extend(missing_collections(ALL, counts))
    return Report(requirements=ALL, counts=counts, problems=problems)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="requirements-to-test traceability report")
    parser.add_argument("--out", default="docs/reports/traceability.md", help="markdown output")
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = parser.parse_args(argv)

    report = build()
    if report.problems:
        for problem in report.problems:
            print(f"problem: {problem}", file=sys.stderr)
    if args.check:
        print(f"traceability: {'clean' if not report.problems else 'PROBLEMS'}")
        return 1 if report.problems else 0

    target = ROOT / args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(report), encoding="utf-8")
    print(f"wrote {args.out} ({sum(report.counts.values())} collected tests referenced)")
    return 1 if report.problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
