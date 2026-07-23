# Issues backlog (local mirror of GitHub issues)

**Item:** Phase 0 — [2] Create GitHub issues from this TODO and assign owners

This directory is the offline mirror of the GitHub issue tracker. Every item
of the proposal's section 15 TODO (`SE_Proposal_v4.tmp`) is one issue, keeps
its proposal number (`1`–`98`) as stable ID, and has an owner.

- `BACKLOG.md` — the full issue list, grouped per phase, with owners/labels.
- Sync to GitHub later (requires auth/publishing, deliberately not done in this
  offline baseline) with e.g.:

  ```bash
  gh issue create --title "[P0-1] Approve proposal and freeze MVP scope" \
    --label "phase-0,docs" --assignee amir-khoshdel-louyeh \
    --body "See docs/issues/BACKLOG.md item 1."
  ```

## Owners

| Code | Team member | Role focus |
|---|---|---|
| `A` | Amir Khoshdel Louyeh (557530) | engine, DSL, executor, bus, planner, sandbox |
| `R` | Alireza Refahifard (557192) | API, auth/RBAC, policy/approvals, audit, security, docs |

Items marked `both` require sign-off from both members (used for approval,
conventions and release gates).

## Labels

`phase-0` … `phase-10`, plus an area label:
`area:docs`, `area:api`, `area:engine`, `area:safety`, `area:bus`,
`area:storage`, `area:planner`, `area:memory`, `area:ui`, `area:security`,
`area:qa`.

## Rules

1. An issue is closed only when its proposal acceptance criterion is met and
   a commit references the item number (`[N]` in the commit subject).
2. A phase is marked **OK** in the proposal only when every issue of that
   phase is closed **and** the phase gate passed (see
   `docs/PROPOSAL_APPROVAL.md` §5).
3. Status is mirrored in `docs/IMPLEMENTATION_STATUS.md`.
