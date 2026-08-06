# Risk register (deliverable §14.4)

**Source of the risk list:** `SE_Proposal_v4.tmp` §16. **Purpose of this file:**
carry each risk from "we thought about it" to "here is the mitigation, the
evidence and what is still exposed". A risk with a mitigation but no evidence is
marked as such — that distinction is the point of the register.

Format: risk · probability/impact (as proposed) · mitigation in the build ·
evidence · **residual exposure** · owner.

---

## 1. Risks from the proposal

| # | Risk | P/I | Mitigation in this build | Evidence | Residual exposure |
|---|---|---|---|---|---|
| R1 | Scope grows beyond two students | High/High | frozen MVP with numbered items 1–98, an explicit `STRETCH` label, and scope guards in the DSL (5 node types, 3 triggers) | `docs/PROPOSAL_APPROVAL.md`, `src/engine/dsl/grammar.py`, `docs/LIMITATIONS.md` §1 | none open: out-of-scope work is refused or labelled |
| R2 | LLM returns invalid or unsafe output | High/High | typed plan → same loader/validator/policy as hand-written YAML; `rm -f /etc/passwd` is planned then `DENY`ed; adapter is schema-validated, timeout/retry bounded, off by default | `src/orchestration/plan_compiler.py`, `tests/acceptance/test_planned_workspace.py`, `tests/unit/test_llm_adapter.py` | adapter untested against a live provider (no key in CI) |
| R3 | Redis duplicate delivery | Medium/High | correlation id + idempotency key; processed-key ledger; durable step state **before** the ack | `src/bus/consumer.py`, `src/bus/workers/core.py`, `tests/unit/test_bus_idempotency.py`, `tests/integration/test_bus_integration.py` | none observed in the tested paths |
| R4 | Worker crash leaves partial state | Medium/High | node state machine with terminal states; reverse-order compensation; retry + DLQ; replay from the pinned version; kill-mid-run test | `src/engine/executor/{states,compensate,scheduler}.py`, `tests/unit/test_engine_phase2.py` | no full chaos suite (`docs/LIMITATIONS.md` §4) |
| R5 | GUI automation is unreliable | High/Medium | GUI nodes are explicit stubs behind a frozen worker contract; they are not on the MVP acceptance path | `src/bus/workers/stubs.py`, `tests/unit/test_bus_stubs.py` | the stub is not a working adapter (deliberate) |
| R6 | External source has an incompatible licence | Medium/High | licence inventory with permitted-use per source; two sources with no licence file are copy-blocked; no runtime import from `source_MIT/` | `docs/THIRD_PARTY_NOTICES.md` §A, the two required greps | two upstream URLs still unverified in the snapshot |
| R7 | Secrets leak in logs or memory | Medium/High | JSON logs with key + rule redaction and a peer-address digest; PII scrub before persistence; secret scan in the export step; no raw provider payloads stored | `src/app/logging.py`, `src/memory/scrub.py`, `docs/reports/secret-scan.txt` (304 files, 9 rules, clean) | redaction is rule-based, not ML-based |
| R8 | PostgreSQL/Redis unavailable during the demo | Medium/High | hermetic acceptance tests need no services; the rehearsal transcript is the backup; `docs/DEMO_SCRIPT.md` §3 lists the fallbacks | `scripts/rehearse_demo.sh`, `docs/reports/rehearsal.txt`, `docs/DEMO_SCRIPT.md` | the *video* itself is not recorded yet (item [95]) |
| R9 | AI-generated code is incorrect | High/Medium | prompt log, per-change review checklist, 1083 tests, coverage gates, adversarial suites, and recorded defects that became regression tests | `docs/AI_USAGE_LOG.md`, `docs/REVIEW_CHECKLISTS.md`, `docs/reports/export.txt` | the reviewer is also the author's teammate: no independent third-party review |

## 2. Risks found **while building** (not in the proposal)

| # | Risk | P/I | What happened | Mitigation now in the build | Evidence | Residual exposure |
|---|---|---|---|---|---|---|
| R10 | A gate that reads the wrong shape allows a dangerous node | would have been High/High | the scheduler gated a nested `params` spec, i.e. an empty command | `gate.flatten_node` normalises, the gate fails closed on unreadable specs, and the shell worker re-decides | `src/engine/executor/gate.py`, `tests/unit/test_engine_gate.py` | a new node type must still be added to the policy rules — reviewed per change |
| R11 | Deployment loses state on container recreation | Medium/High | the item [88] verification wiped SQLite with the container | named volume for `/data`, deployment re-verified end to end, backup/restore scripted | `compose.yml`, `docs/DEPLOYMENT.md` §2.2, `docs/reports/rehearsal.txt` | verified on one host only |
| R12 | A security artefact that is not machine-readable | Medium/Medium | Bandit JSON was unparseable; the CSV replacement counted Bandit's own log lines as findings | CSV with the log stream separated, header-anchored reader, threshold recorded in the summary, regression test | `scripts/bandit_summary.py`, `tests/unit/test_bandit_summary.py`, `docs/reports/bandit-summary.json` | none known |
| R13 | An evidence artefact silently hides its own result | Medium/Medium | a duplicated `-q` silenced pytest's totals line in `pytest.txt` | export step fixed; the export summary is itself an artefact (`export.txt`) | `scripts/export_reports.sh`, `docs/reports/export.txt` | — |
| R14 | Reference material creeps into the build | Medium/High | not observed; the two greps are part of the check | greps in `docs/THIRD_PARTY_NOTICES.md`, `source_MIT/` gitignored and excluded from the image | same file | a manual adaptation is still a human judgement call |

## 3. Risk scoring used here

- **Probability/impact** are the proposal's (§16), kept unchanged so the
  register can be compared with the plan line by line.
- A risk is **closed** only when the mitigation has a test, a script or a
  measured report behind it. Otherwise it is **mitigated with stated exposure**,
  which is what every row above says.
- Any new risk found during review or a usability session is appended to §2 with
  the item that fixed it — the same rule `docs/LIMITATIONS.md` §8 uses.

## 4. Open exposures, restated in one place

1. The SUS sessions (item [87]) have not run — the only NFR without measured
   evidence.
2. The three-minute demo video is scripted and rehearsed, not recorded (item
   [95]).
3. The deployment is verified on one host; no independent environment run.
4. No full chaos suite (broker/database/worker kill loops) and no external
   audit anchor.
5. No third-party review: the same two people reviewed every commit.
