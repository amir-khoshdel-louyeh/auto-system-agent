#!/usr/bin/env bash
# Phase 10 item [93]: export the test, coverage, security and performance
# reports that back the SE package.
#
# Every artefact under docs/reports/ is produced by this script, so a number
# in the documentation can always be traced to the command that measured it:
#
#   pytest.txt        test run transcript + totals
#   coverage.txt      coverage summary (global and the strict core gate)
#   coverage.xml      machine-readable coverage
#   bandit.txt        static analysis, text + the `nosec` audit trail
#   bandit.csv        the report alone (no log lines)
#   bandit-summary.json  counts + the threshold they were counted at
#   pip-audit.txt     dependency audit for all three requirement sets
#   secret-scan.txt   repository secret scan
#   diagrams.txt      mermaid render check
#   perf.txt/json     API p95 and worker throughput
#   traceability.md   requirements-to-test mapping (regenerated)
#   rehearsal.txt     clean-environment rehearsal transcript
#
# Usage:  bash scripts/export_reports.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="docs/reports"
mkdir -p "$OUT"

declare -a NAMES=() STATUS=()
FAILURES=0

capture() {
  local name="$1"; shift
  local target="$1"; shift
  printf '\n=== %s -> %s\n' "$name" "$target"
  if "$@" >"$target" 2>&1; then
    printf -- '--- %s: PASS (%s)\n' "$name" "$target"
    NAMES+=("$name"); STATUS+=("PASS")
  else
    printf -- '--- %s: FAIL (%s) — inspect the file\n' "$name" "$target"
    tail -5 "$target"
    NAMES+=("$name"); STATUS+=("FAIL")
    FAILURES=$((FAILURES + 1))
  fi
}

{
  echo "report export $(date -Iseconds)"
  echo "commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout')"
  echo "python: $(python -V 2>&1)"

  # Tests: the transcript is the artefact, the totals line is the claim.
  # (No extra -q: addopts already sets it, and a second one silences the
  # summary line the artefact exists to show.)
  capture "tests" "$OUT/pytest.txt" python -m pytest tests --no-header

  # Coverage: global ratchet plus the strict core gate (engine|safety|storage|bus).
  capture "coverage (global)" "$OUT/coverage.txt" \
    python -m pytest tests --cov=src --cov-report=term --cov-report=xml
  capture "coverage (core gate)" "$OUT/coverage-core.txt" \
    python -m pytest tests --cov=src/engine --cov=src/safety --cov=src/storage --cov=src/bus \
      --cov-report=term

  # `bash -c` keeps Bandit's log lines out of the CSV: the report goes to
  # stdout (the capture) and the noisy `[tester] WARNING nosec ...` stream is
  # dropped here — it stays in bandit.txt, where a human reads it.
  capture "bandit (text)" "$OUT/bandit.txt" bandit -r src -ll -ii
  # Bandit 1.9.4's JSON output is not always parseable (raw control chars in
  # the source snippet); CSV is, so the JSON summary is built from CSV by
  # scripts/bandit_summary.py — without the snippet.
  capture "bandit (csv)" "$OUT/bandit.csv" \
    bash -c 'bandit -r src -ll -ii -f csv 2>/dev/null'
  capture "bandit (summary)" "$OUT/bandit-summary.json" \
    python scripts/bandit_summary.py "$OUT/bandit.csv" "$OUT/bandit-summary.json"

  {
    echo "pip-audit $(pip-audit --version 2>&1)"
    for requirements in requirements.txt requirements-api.txt requirements-dev.txt; do
      echo "--- $requirements"
      pip-audit -r "$requirements"
    done
  } >"$OUT/pip-audit.txt" 2>&1
  if grep -q "No known vulnerabilities found" "$OUT/pip-audit.txt"; then
    printf -- '--- pip-audit: PASS\n'; NAMES+=("pip-audit"); STATUS+=("PASS")
  else
    printf -- '--- pip-audit: FAIL — see %s\n' "$OUT/pip-audit.txt"
    NAMES+=("pip-audit"); STATUS+=("FAIL"); FAILURES=$((FAILURES + 1))
  fi

  capture "secret scan" "$OUT/secret-scan.txt" python scripts/secret_scan.py
  capture "diagram validation" "$OUT/diagrams.txt" python scripts/validate_diagrams.py

  capture "performance benchmark" "$OUT/perf.txt" \
    python scripts/benchmark.py --json "$OUT/perf.json"

  capture "traceability report" "$OUT/traceability-run.txt" python scripts/traceability_report.py

  if [[ "${SKIP_REHEARSAL:-0}" != "1" ]]; then
    export REPORT_PATH="$OUT/rehearsal.txt"
    capture "rehearsal" "$OUT/rehearsal-run.txt" bash scripts/rehearse_demo.sh
    unset REPORT_PATH
  else
    echo; echo "=== rehearsal: SKIPPED (SKIP_REHEARSAL=1)"
    NAMES+=("rehearsal"); STATUS+=("SKIP")
  fi

  printf '\nsummary\n-------\n'
  for index in "${!NAMES[@]}"; do
    printf '%-24s %s\n' "${NAMES[$index]}" "${STATUS[$index]}"
  done
  printf '\n%s step(s) failed; artefacts in %s/\n' "$FAILURES" "$OUT"
} 2>&1 | tee "$OUT/export.txt"

exit "$FAILURES"
