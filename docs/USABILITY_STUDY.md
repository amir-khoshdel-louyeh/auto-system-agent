# Usability study — protocol, instrument and results

**Item:** Phase 9 — [87] Run a SUS study with five users
**Status:** protocol, instrument and scoring tool are ready and tested;
**the five sessions have not been run yet** (they need people in front of a
screen). Item [87] closes only when §6 is filled in with real session data.

The point of the study is narrow and honest: does a first-time user complete
the five client tasks without a moderator's help, and how does the client
score on the standard System Usability Scale? It is not a usability
*wishlist* exercise, and no participant data is stored in the repository.

---

## 1. Participants and ethics

- **Five participants** (the proposal's number), each new to the system.
  Mix at least one non-developer; none of them worked on the project.
- **Consent, in writing, before the session** (template below): purpose,
  duration (~25 min), what is recorded, and the right to stop at any time.
- **No PII in the results.** Participants appear as `P1`…`P5`; the only
  stored data is task success/failure, time on task, the ten SUS answers and
  free-text comments that the participant agrees to.
- The session runs against a **local stack** (`docs/DEPLOYMENT.md`) with
  seeded data; no live credentials, no real workflows.

Consent sentence for the form:

> I agree to take part in a 25-minute usability session for the Auto System
> Agent. I understand my actions and answers will be recorded against a
> participant number, that no personal data will be stored, and that I may
> stop at any time without giving a reason.

## 2. Tasks (one per client screen)

The moderator reads each task aloud, starts the clock, and **does not help**
for the first 3 minutes; after that a single hint ("look at the Approvals
tab") is allowed and recorded as *assisted*, not *success*.

| # | Task | Screen | Success criterion |
|---|---|---|---|
| T1 | "Check whether `workflows/valid-minimal.yaml` is a usable workflow, and tell me what it would do before it runs." | Workflow | Opens the DSL, gets `OK <name> v<version>`, runs the dry run, and reads at least one per-node verdict + the `safe_to_run` summary |
| T2 | "Find the run that finished most recently and tell me which step failed and why." | Runs | Selects the newest run, opens its timeline, names the node and the error text |
| T3 | "Approve the pending request that needs two approvals, and say what happens to it." | Approvals | Selects the request, gives it a reason, presses Approve, and states that the request is still PENDING until the second approver decides |
| T4 | "Show me the audit evidence for that run and tell me whether it has been tampered with." | Audit | Loads the chain for the run and reads the verification result (OK or the first broken sequence) |
| T5 | "Someone asks whether `rm -rf /` would be allowed. Answer without running anything." | Workflow | Uses the dry run / policy explanation and reports DENY with the reason, without executing |

Record per task: `success` / `assisted` / `failed`, seconds on task, and the
participant's own words for anything confusing.

## 3. Instrument (System Usability Scale)

Administered immediately after T5, on paper or a form, **without** the
moderator present. Scale: 1 = strongly disagree … 5 = strongly agree.

1. I think that I would like to use this system frequently.
2. I found the system unnecessarily complex.
3. I thought the system was easy to use.
4. I think that I would need the support of a technical person to be able to use this system.
5. I found the various functions in this system were well integrated.
6. I thought there was too much inconsistency in this system.
7. I would imagine that most people would learn to use this system very quickly.
8. I found the system very cumbersome to use.
9. I felt very confident using the system.
10. I needed to learn a lot of things before I could get going with this system.

```bash
python scripts/sus_score.py --template        # print the blank form
python scripts/sus_score.py responses.json    # score the study
```

Scoring (implemented in `scripts/sus_score.py`, tested in
`tests/unit/test_sus_score.py`): odd items contribute `response − 1`, even
items `5 − response`; the ten contributions are summed and multiplied by 2.5.
Reference points: **68 is the accepted average**, 80.3 is an A. The tool
prints the per-participant scores, mean, median, standard deviation and
range, and exits non-zero if the mean misses 68 — so the result cannot be
quietly rounded up.

## 4. Analysis

- Quote the **mean ± spread**, never the mean alone: 3 × 100 and 2 × 0 has a
  mean of 60 that describes nobody.
- Report task success as `x/5` per task; `failed` or `assisted` are the
  interesting rows, and each one needs the participant's comment.
- Classify every comment as *defect* (fix in the MVP), *paper cut*
  (document), or *out of scope* (record in `docs/LIMITATIONS.md`).
- Two or more participants failing the same task is a defect, not a training
  problem — fix the screen or the message before the defense.

## 5. What "done" looks like for item [87]

1. Five completed consent forms (filed outside the repository).
2. A filled §6 table below.
3. `responses.json` scored by the script, with the raw output pasted in §6.
4. Every defect raised is either fixed (with the commit) or listed in
   `docs/LIMITATIONS.md`.

## 6. Results

> **Not yet run** — fill this in from the sessions. Do not delete the
> "pending" marker until all five rows exist.

| Participant | T1 | T2 | T3 | T4 | T5 | SUS score | Notes |
|---|---|---|---|---|---|---|---|
| P1 | pending | pending | pending | pending | pending | — | — |
| P2 | pending | pending | pending | pending | pending | — | — |
| P3 | pending | pending | pending | pending | pending | — | — |
| P4 | pending | pending | pending | pending | pending | — | — |
| P5 | pending | pending | pending | pending | pending | — | — |

```bash
$ python scripts/sus_score.py responses.json
# paste the output here after the sessions
```

**Study outcome:** pending — the client's usability claim is not made until
these rows are real.
