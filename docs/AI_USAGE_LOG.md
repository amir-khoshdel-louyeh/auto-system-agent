# AI-assisted development — prompt log and review record (item [98])

**Deliverable:** proposal §14.8 ("prompt log and review checklist for AI-assisted
code"). The checklist itself is `docs/REVIEW_CHECKLISTS.md`; this file is the
log it is applied to.

## 0. How to read this file (and what it does not claim)

- **Provenance is labelled per row.** A row marked **raw export** has a verbatim
  session transcript in `docs/prompts/`; a row marked **reconstructed** was
  rebuilt from the git history and the session notes of the day, and says so.
  We would rather label the gap than present a reconstruction as a transcript.
- **The assistant is the primary code author of this repository.** That is the
  honest statement, and it is why the log, the review checklist and the test
  gates exist: they are the control, not a formality.
- **No secrets, no personal data.** The log stores requests, decisions and
  outcomes — never provider keys, tokens or personal data. `scripts/secret_scan.py`
  scans `docs/` too (9 rules, 278 files, clean in item [93]).
- **A prompt is not evidence.** A row is only useful together with the commit
  that closed the item and the test that proves it, so both are in the table.

## 1. The working method, in one picture

```text
proposal item [N]
      │  prompt: "implement [N] per proposal §…, keep it verifiable"
      ▼
assistant proposes code + tests
      │  human review (docs/REVIEW_CHECKLISTS.md §2): run it, read it, break it
      ▼
red / green  ──fail──▶ fix or reject the change, prompt again (logged)
      │
      ▼
ruff + mypy + full suite + coverage gate + bandit + secret scan
      │
      ▼
commit referencing [N]  →  docs/IMPLEMENTATION_STATUS.md row  →  docs/TRACEABILITY.md evidence
```

The loop is deliberately boring: an AI-authored change has no shortcut past a
failing test, a coverage gate or a human who can say "no".

## 2. Per-phase log

| Phase | Items | Prompt entries (summary) | Source | Review decision | Defects caught by review/tests | Evidence commits |
|---|---|---|---|---|---|---|
| 0 | 1–9 | freeze scope; scaffold tests/config/CI; write README + traceability matrix | reconstructed | accepted after README and CI were run on a clean checkout | coverage config initially below the 80% floor on `src/`| `db260cb`, `66e3a16`, `7ffed59`, `3617a66`, `f971668` |
| 1 | 10–17 | settings validation; JWT + PBKDF2; RBAC matrix; migration runner; repository interfaces | reconstructed | accepted; env-admin replaced by DB users after review | migration runner had to be made idempotent with checksums (drift detection added) | `aa2d56e`…`f971a2e` |
| 2 | 18–26 | freeze DSL; typed loader with positions; 5-stage validator; Kahn order; endpoint | reconstructed | accepted after the 13 fixtures were hand-checked for expected findings | error payload shape changed twice to keep `stage/line/column` stable | `1cb58de`…`5be1075` |
| 3 | 27–36 | version pinning; node state machine; concurrency; retry/timeout; compensation; policy service | reconstructed | accepted; the policy service was extracted on review feedback | the scheduler gate read nested params → **allowed a dangerous node**; fixed by flattening and fail-closed (kept as a regression test) | `4a8757c`…`c8af776`, `9c9224b` |
| 4 | 37–45 | frozen event schemas; producer/consumer group; idempotency; shell/approval workers; DLQ; triggers | reconstructed | accepted; GUI/custom workers forced to be *explicit* stubs, never silent no-ops | step state had to move *before* the ack (order-of-effects bug) | `3a4ec2d`…`7906156` |
| 5 | 46–54 | append-only audit + hash chain; query/timeline endpoints; run metrics; replay + normalisation | reconstructed | accepted; a first replay version compared raw dicts and was replaced by normalization | tamper test initially missed a *relinked* chain; deletion and relink cases added | `cdf611d`…`5b81543`, `8aba175` |
| 6 | 55–63 | intents/slots; labelled dataset; normalize→rules→slots; clarification; typed LLM adapter; eval | reconstructed | accepted after the dataset was reviewed by hand for label errors | adapter returned provider payloads in metadata → stripped to secret-free fields | `d4c1874`…`e81699a` |
| 7 | 64–72 | consent + revocation; PII scrub; deterministic embeddings; retrieval filters; reputation; quarantine states; erasure; reuse metrics | reconstructed | accepted; promotion path narrowed to `QUARANTINED→SANDBOXED→PUBLIC` | scrub initially ran *after* the consent check in one path — reordered, tested | `45c2e65`…`a6d33e2` |
| 8 | 73–80 | sandbox contract + limits; plugin manifest validation; sandboxed custom nodes; Tkinter screens; policy explanations; dry run | reconstructed | accepted after every escape vector was attempted in a test | `bwrap` absent ⇒ `command` nodes must be refused, not run unguarded; two AST-based escape cases failed first | `b347832`…`b6217e1` |
| 9 | 81–89 | STRIDE model; Bandit/pip-audit/secret scan; prod compose; rate limits; log redaction; benchmark; SUS kit; compose verification; backup/restore | partly raw export | accepted; deployment verification reopened once (state loss) | compose redeploy wiped SQLite state (container layer) → named volume + re-verified rebuild | `305d26c`…`9c…94da58a`, `2419eef`, `c561f1d` |
| 10 | 90–98 | diagrams; traceability report; report export; limitations; demo script; rehearsal; Q&A; this log | **raw export** (`docs/prompts/session-2026-10-18-phase-10.md`) | in review at freeze: every number regenerated by `scripts/export_reports.sh` before the tag | Bandit summary counted Bandit's own log lines as findings (0 real findings at the gate threshold, 37 reported); `pytest -q` duplicated in the export silenced the very summary the artefact must show; a placeholder assertion left in a new test | `641d769`, `bcd2ade`, `630ce61`, this commit |

## 3. Defects an AI-authored change really produced (kept as evidence)

These are the ones worth naming in a defense, because each is now a test or a
script rather than a memory:

1. **Gate bypass on nested specs** (phase 3) — the compiled node nested `params`,
   the policy service read a flat spec, so the gate inspected an empty command
   and would have allowed a dangerous node. Fixed in `9c9224b`, tested in
   `tests/unit/test_engine_gate.py`.
2. **Durable state written after the verdict** (phase 4) — a crash between
   execution and audit could lose an effect. Fixed by persisting before
   publish/ack (`src/bus/workers/core.py`, item [43]).
3. **State loss on redeploy** (phase 9) — the compose stack put SQLite in the
   container layer; found by the item [88] verification, fixed with a named
   volume and re-verified.
4. **Unparseable security artefact** (phase 10) — Bandit's JSON was not
   machine-readable, and the CSV-derived replacement counted Bandit's log lines
   as findings. Fixed with a header-anchored, noise-filtered reader plus a
   regression test (`tests/unit/test_bandit_summary.py`).
5. **Evidence artefact that hid its own result** (phase 10) — a duplicated `-q`
   in the export step silenced pytest's summary line, so `pytest.txt` could not
   show pass counts. Fixed in `scripts/export_reports.sh`.
6. **A placeholder assertion** (phase 10) — a new test shipped with a leftover
   `if False else` expression; caught in review before commit.

## 4. How the log stays honest

- Every row must name at least one commit **and** one test path; a row without
  either is a plan, not a record.
- Defects are recorded with the item that fixed them; a defect found after the
  commit is added to §3 rather than being quietly fixed.
- Rows are labelled **reconstructed** where no raw transcript was retained —
  the phases before the log existed are not dressed up as verbatim sessions.
- The usability study (item [87]) and the demo recording (item [95]) are human
  tasks: no AI row claims to have produced their *results*.
