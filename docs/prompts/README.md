# Prompt transcripts (archive convention)

**Item:** Phase 10 — [98] Archive prompt logs and human review checklists.
**Companion:** `docs/AI_USAGE_LOG.md` (per-phase record) and
`docs/REVIEW_CHECKLISTS.md` (the review that was applied).

## What lives here

One Markdown file per working session that used an AI assistant, named
`session-<YYYY-MM-DD>-<phase>.md`, containing:

1. **session header** — date, phase/items in scope, tooling, commit range;
2. **prompts as issued** — verbatim where the session was exported, otherwise a
   clearly labelled summary;
3. **what the assistant produced** — files, tests, scripts, with paths;
4. **what the human changed, rejected or re-asked** — the decision record;
5. **defects found** — the ones §3 of the usage log cites;
6. **verification run** — the commands and their totals.

## Redaction rules (they are not optional)

- Never paste provider keys, tokens, passwords, `.env` contents or personal
  data into a transcript. Replace them with `<redacted>`.
- Keep secrets out of prompts in the first place: the session reads
  `.env.example`, never `.env` (the tooling refuses `.env` files for this
  reason).
- `scripts/secret_scan.py` scans `docs/` as part of the export step; a
  transcript that trips it is fixed, not whitelisted.
- Large scratch output is trimmed with a `[… 200 lines elided …]` marker rather
  than deleted, so a reader can see that something was there.

## Retention

- Transcripts are kept for the lifetime of the project: they are the evidence
  behind `docs/AI_USAGE_LOG.md`.
- Sessions before 2026-10-18 were not exported verbatim; the usage log marks
  those rows **reconstructed** (from git history and session notes) and this
  archive starts with the Phase 10 session.
