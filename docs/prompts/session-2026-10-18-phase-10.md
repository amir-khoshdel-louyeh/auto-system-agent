# Session 2026-10-18 — Phase 10 (items [90]–[98])

**Phase / items in scope:** 10 — final SE package and defense rehearsal.
**Tooling:** coding assistant in the repository (read/edit/execute), Python
3.14.8, pytest + coverage, ruff, mypy, Bandit, pip-audit, `bwrap` available on
this host, Redis reachable for the gated bus tests.
**Commit range:** `641d769` (diagrams) → the `docs(phase10)` freeze commit.

## 1. Prompts as issued

Prompts from the first half of the session were condensed before this archive
existed, so they are recorded as summaries and labelled as such. Prompts from
the second half are verbatim.

| # | Prompt | Source |
|---|---|---|
| 1 | "continue: close Phase 10 — complete the UML/architecture diagrams, then the traceability matrix" | summary |
| 2 | "make the traceability machine-checked: every requirement must cite modules and tests that actually exist and actually collect" | summary |
| 3 | "export the test, coverage, security and performance reports so every number in the docs comes from a script" | summary |
| 4 | "write the limitations and deferred-feature section — only things the project *knows*" | summary |
| 5 | "rehearse W1/W2/W3 from a clean environment and keep the transcript" | summary |
| 6 | **"complete the phases 9 and 10, then we start solving the limitations"** | verbatim |
| 7 | **"yes, continue please"** | verbatim |

## 2. What the assistant produced

| Artefact | Purpose |
|---|---|
| `docs/ARCHITECTURE.md` (diagram additions) + `scripts/validate_diagrams.py` | UML/architecture, sequence and state diagrams that are *rendered* by mermaid-cli in CI-style checks, not just written |
| `docs/TRACEABILITY.md` + `scripts/traceability_report.py` + `tests/unit/test_traceability_report.py` | requirements → modules → tests, machine-checked (`--check`), with the generated companion in `docs/reports/traceability.md` |
| `scripts/export_reports.sh` | one command that regenerates pytest, coverage (global + core gate), Bandit (text/CSV/summary), pip-audit, secret scan, diagram check, perf, traceability and rehearsal artefacts |
| `scripts/bandit_summary.py` + `tests/unit/test_bandit_summary.py` | a security artefact that is valid JSON and states the threshold it counted |
| `docs/LIMITATIONS.md` | deliberate boundaries, architectural limits, testing gaps, privacy, platform, deferred features |
| `scripts/rehearse_demo.sh` | clean-room rehearsal of W1/W2/W3 plus the gates they lean on, one PASS/FAIL line per step, transcript kept |
| `docs/DEMO_SCRIPT.md` | the three-minute backup video script with timings, exact commands and a failure-fallback table |
| `docs/DEFENSE_QA.md` | prepared answers for safety, AI ownership, reuse, failure recovery and scalability |
| `docs/AI_USAGE_LOG.md`, `docs/REVIEW_CHECKLISTS.md`, `docs/prompts/` | this archive and the checklist it is reviewed with |
| `docs/RELEASE_NOTES.md` | the frozen release candidate, its claims and its deferred features |

## 3. Human decisions (what was changed, rejected or re-asked)

- **Rejected as unverifiable:** an early version of the traceability report that
  listed requirements without checking that the cited test files *collect*;
  the final script asserts existence **and** collection, and fails otherwise.
- **Re-asked:** the first Bandit JSON artefact was machine-unreadable; the
  response (CSV-derived summary) then over-counted, which led to the review
  below and a stricter reader.
- **Changed by the human:** the limitations document was pruned to items with a
  named compensation *and* a named fix location — anything else was moved into
  the testing-gaps table instead of being presented as a product limitation.
- **Kept deliberately:** the rehearsal script reports `SKIP` for the live API
  step under `--quick` rather than silently passing; skipped is never counted
  as passed.
- **Human-only tasks left open:** the SUS sessions (item [87]) and the actual
  recording of the demo video (item [95]). The assistant prepared protocol,
  scorer and script; it does not claim their *results*.

## 4. Defects found during this session (all in review, before the tag)

1. `scripts/bandit_summary.py` counted Bandit's own log lines (`[tester] WARNING
   nosec …`, the progress bar) as findings: it reported **37 findings, 0 medium
   or higher** when the truth at the gate threshold was **0 findings**. Fixed by
   anchoring on the CSV header row and requiring a real `B<digits>` test id, with
   `tests/unit/test_bandit_summary.py::…::test_log_lines_and_duplicates_are_not_findings`.
2. The export step ran `pytest -q` while `addopts` already contained `-q`, which
   silenced the totals line in the very artefact that exists to show it. Fixed in
   `scripts/export_reports.sh`.
3. A new test shipped with a leftover placeholder expression
   (`self.assertRaises(SystemExit) if False else …`); removed in review.
4. A Bandit CSV captured with `2>&1` interleaved logs into the report file; the
   export step now redirects the log stream away from the CSV.
5. `docs/ARCHITECTURE.md` mermaid fences did not render until a stray fence and a
   node-id collision were fixed — caught by `scripts/validate_diagrams.py`.

## 5. Verification run (repeated at the freeze commit)

```bash
ruff format --check . && ruff check .       # 254 files formatted / All checks passed!
mypy src                                    # no issues in 118 source files
python -m pytest tests                      # 1055 passed, 2 skipped, 767 subtests
bash scripts/export_reports.sh              # 12 steps, 0 failed (docs/reports/export.txt)
```

Resulting artefacts: global coverage **80.75%** (gate `fail_under = 78`), core
gate `src/engine|safety|storage|bus` **86.12%**, worst API p95 **36.44 ms** vs
the 300 ms NFR, Bandit **0** at medium+, `pip-audit` clean on three requirement
sets, secret scan clean (278 files, 9 rules), traceability `--check` clean,
rehearsal **12/12 PASS**. The two tests added during the session (bandit log-line
filtering and the threshold field) are why the count moved from 1053 to 1055.

## 6. Post-session corrections to this archive (made in `[92]`, same day)

- §4.1 was clarified to name the threshold: the corrected summary reports **0**
  findings *at `-ll -ii`*, which is the gate the release claim is about, not
  "Bandit found nothing at any level".
- `docs/RELEASE_NOTES.md` was written **after** this session and is the file the
  archive's claims are frozen against; where the two disagree, the release notes
  win and this file is corrected.
