# Human review checklists (item [98])

**Deliverable:** proposal §14.8 — "prompt log and review checklist for
AI-assisted code". The log these checklists were applied to is
`docs/AI_USAGE_LOG.md`; the conventions they extend are `docs/CONVENTIONS.md` §4.

A checklist that is never filled in is decoration. Every phase that closes gets
a signed row in §5, and the two `PENDING` rows there are the reason the release
candidate is called a release candidate.

---

## 1. Before every commit (mechanical — the assistant must run these)

- [ ] `ruff check .` and `ruff format --check .` clean.
- [ ] `mypy src` clean.
- [ ] `python -m pytest tests` green, with the totals line pasted into the
      commit discussion (not "the tests pass").
- [ ] Coverage floors respected: global ≥ 78%, core gate
      (`src/engine|safety|storage|bus`) ≥ 80%.
- [ ] New behaviour has a test that fails without it (state the check you ran to
      see it fail).
- [ ] No secret, token, `.env` content or personal data in the diff; `python
      scripts/secret_scan.py` clean.
- [ ] Commit message references the TODO item `[N]` and matches
      `docs/CONVENTIONS.md` §3.

## 2. Reviewing an AI-assisted change (human — cannot be delegated to a script)

Read the diff, then answer each line *yes/no*. A single "no" blocks the commit.

1. **Did I run it myself**, on a clean checkout, and see the claimed output?
2. **Is the claim measured?** If a number appears in a doc or a docstring, is
   there a command in `docs/reports/` (or a test) that produced it?
3. **Does the test actually test?** Would it fail if I removed the behaviour?
   (Try one: delete the guard, watch it go red, restore it.)
4. **Are failure paths real?** No bare `except: pass`, no swallowed error, no
   assertion weakened to make a suite green, no type/lint suppression without a
   written reason.
5. **Does it fail closed?** For anything security-relevant, an unreadable input
   must deny, not allow.
6. **Is the boundary honest?** New code must not claim more than it does —
   record-only is called record-only, a stub is called a stub, an unverified
   upstream URL is called unverified.
7. **Is the dependency justified?** Nothing new is imported unless it is already
   used in the project or its licence and version are recorded in
   `docs/THIRD_PARTY_NOTICES.md`.
8. **Does it touch an earlier phase's contract?** If yes, the earlier phase's
   tests must be run and the phase log updated — a later phase may not hide an
   incomplete earlier one.
9. **Is the human able to explain it?** If I cannot explain the change aloud in
   the defense, it does not go in.
10. **Is the documentation updated in the same commit?** Status row, traceability
    row, and any number that the change invalidates.

## 3. Phase-gate checklist (closing a phase)

- [ ] Every item `[N]` in the phase has a commit that references it.
- [ ] `docs/IMPLEMENTATION_STATUS.md` row states the gate evidence with real
      numbers, not adjectives.
- [ ] `docs/TRACEABILITY.md` rows for the phase's requirements cite tests that
      exist and collect.
- [ ] `docs/reports/export.txt` regenerated after the last commit of the phase,
      all gates PASS (or explicitly SKIPped with a reason).
- [ ] Known gaps moved into `docs/LIMITATIONS.md` instead of being omitted.
- [ ] Anything a human still has to do is labelled `PENDING (human)` — never
      silently marked OK.

## 4. Release-candidate checklist (item [92])

- [ ] `git status` clean; every artefact under `docs/reports/` comes from
      `scripts/export_reports.sh`, regenerated at the tagged commit.
- [ ] Numbers in `docs/RELEASE_NOTES.md`, `docs/DEMO_SCRIPT.md`,
      `docs/DEFENSE_QA.md` and `docs/IMPLEMENTATION_STATUS.md` agree with
      `docs/reports/*` (grep the numbers, do not trust memory).
- [ ] Traceability `--check` clean; the pending usability area still labelled.
- [ ] `bash scripts/rehearse_demo.sh` 12/12 PASS on the frozen commit.
- [ ] Tag created on the frozen commit and the tag named in the release notes.
- [ ] Rollback story stated: what to do if the demo fails (see `docs/DEMO_SCRIPT.md` §3).

## 5. Sign-off table

| Phase | Reviewed by | Date | Result | Notes |
|---|---|---|---|---|
| 0–8 | Amir Khoshdel Louyeh, Alireza Refahifard | 2026-10-15 … 2026-10-17 | ACCEPTED | per-phase logs in `docs/IMPLEMENTATION_STATUS.md` |
| 9 | Amir Khoshdel Louyeh, Alireza Refahifard | 2026-10-18 | ACCEPTED except [87] | [87] SUS sessions PENDING (human) |
| 10 | Amir Khoshdel Louyeh, Alireza Refahifard | 2026-10-18 | ACCEPTED except [95] recording | [95] demo script + rehearsal accepted; recording PENDING (human) |
| Security artefacts (Bandit/pip-audit/secret scan) | Amir Khoshdel Louyeh | 2026-10-18 | ACCEPTED | threshold-stated summary, `docs/reports/bandit-summary.json` |
| Numeric claims in the docs | Alireza Refahifard | 2026-10-18 | ACCEPTED | re-derived from `docs/reports/export.txt`, not copied |

**Pending human actions, restated so they cannot be lost:**

1. **Item [87] — SUS study, five sessions.** Protocol, task list, instrument and
   a tested scorer are ready (`docs/USABILITY_STUDY.md`, `scripts/sus_score.py`);
   the results table is empty.
2. **Item [95] — record the three-minute demo video** from
   `docs/DEMO_SCRIPT.md`; the rehearsal transcript already exists, the recording
   does not.
