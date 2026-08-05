#!/usr/bin/env bash
# Phase 10 item [96]: rehearse W1, W2, W3 (and the gates they lean on) from a
# clean environment.
#
# "It worked yesterday" is not evidence. This script runs the acceptance
# scenarios and the supporting gates in a *fresh* temporary directory with a
# fresh database, prints one PASS/FAIL line per step, and writes the whole
# transcript to docs/reports/rehearsal.txt. Run it before the defense, and
# read the table rather than the tail.
#
# Usage:  bash scripts/rehearse_demo.sh            # full rehearsal
#         bash scripts/rehearse_demo.sh --quick    # skip the live API smoke
#         REPORT_PATH=path.txt bash scripts/rehearse_demo.sh   # also tee there
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

WORKDIR="$(mktemp -d -t asa-rehearsal-XXXXXX)"
#: Set REPORT_PATH to also tee the transcript (the export step does this).
REPORT="${REPORT_PATH:-}"

export TMPDIR="$WORKDIR"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/src"
# A clean environment must not inherit a developer database or plugin dir.
unset SQLITE_PATH AUTO_AGENT_PLUGIN_DIR || true

declare -a NAMES=() STATUS=() DETAILS=()
FAILURES=0

step() {
  local name="$1"; shift
  printf '\n=== %s\n' "$name"
  if "$@"; then
    printf -- '--- %s: PASS\n' "$name"
    NAMES+=("$name"); STATUS+=("PASS"); DETAILS+=("$*")
  else
    printf -- '--- %s: FAIL\n' "$name"
    NAMES+=("$name"); STATUS+=("FAIL"); DETAILS+=("$*")
    FAILURES=$((FAILURES + 1))
  fi
}

pytest_step() { python -m pytest "$@" -q; }

{
  echo "rehearsal $(date -Iseconds)"
  echo "host: $(uname -srm) | python $(python -V 2>&1) | workspace $WORKDIR"
  echo "commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout')"

  step "W1 scaffold (mkdir + venv + README, traversal denied)" \
    pytest_step tests/acceptance/test_e2e_workflows.py -k "Scaffold"
  step "W2 install fallback (manager matrix + selector rewrite)" \
    pytest_step tests/acceptance/test_e2e_workflows.py -k "InstallFallback"
  step "W3 log batch (500 logs, zip, checksum, no freeze)" \
    pytest_step tests/acceptance/test_e2e_workflows.py -k "LogBatch"
  step "W1 through the planner, validator, bus and replay" \
    pytest_step tests/acceptance/test_planned_workspace.py
  step "Replay from the pinned version (match vs original)" \
    pytest_step tests/acceptance/test_replay.py
  step "Memory reuse gate (safe plan reused, failed plan quarantined)" \
    pytest_step tests/acceptance/test_memory_reuse_gate.py
  step "Shell nodes execute for real (sandboxed, workspace-bounded)" \
    pytest_step tests/integration/test_shell_execution.py tests/unit/test_sandbox_commands.py
  step "Sandbox gate (untrusted code cannot leave its workspace)" \
    pytest_step tests/acceptance/test_sandbox_isolation.py
  step "Client walkthrough against a live API (four screens)" \
    pytest_step tests/acceptance/test_workbench_walkthrough.py
  step "Approvals + RBAC + audit endpoints" \
    pytest_step tests/integration/test_approvals_api.py tests/integration/test_audit_api.py
  step "Migrations, backup/restore and the upgrade path" \
    pytest_step tests/integration/test_app_migration.py tests/integration/test_backup_restore.py
  step "Security guards (limits, redaction, secret scan, threat model)" \
    pytest_step tests/unit/test_rate_limit.py tests/unit/test_log_redaction.py tests/unit/test_secret_scan.py tests/unit/test_threat_model_doc.py
  step "Client entry point loads without a display" \
    bash -c 'python -m ui.workbench --help >/dev/null'

  if [[ "$QUICK" -eq 0 ]]; then
    step "Live API + worker smoke (login, reads, shell + sandbox workers)" \
      python scripts/benchmark.py --requests 20 --threads 2 --seed-executions 25 \
        --audit-events 10 --worker-events 10 --shell-runs 3 --sandbox-runs 3
  else
    echo; echo "=== live API + worker smoke: SKIPPED (--quick)"
    NAMES+=("Live API + worker smoke"); STATUS+=("SKIP"); DETAILS+=("--quick")
  fi

  printf '\nsummary\n-------\n'
  printf '%-58s %s\n' "step" "result"
  for index in "${!NAMES[@]}"; do
    printf '%-58s %s\n' "${NAMES[$index]}" "${STATUS[$index]}"
  done
  printf '\n%s step(s) failed\n' "$FAILURES"
  printf 'rehearsal workspace: %s (removed by the trap)\n' "$WORKDIR"
} 2>&1 | { if [[ -n "$REPORT" ]]; then tee "$REPORT"; else cat; fi; }

rm -rf "$WORKDIR"
exit "$FAILURES"
