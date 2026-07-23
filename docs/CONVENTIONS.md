# Coding, branching, review and commit conventions

**Item:** Phase 0 — [4] Define coding, branching, review and commit conventions
**Applies to:** every contribution to this repository (humans and AI agents).

---

## 1. Coding conventions

### 1.1 Layout

- Production code lives under `src/` only (`src`-only rule from `plan.tmp`).
  No new top-level package directories; each `src/<pkg>/` maps to a
  proposal §8.2 responsibility (`app`, `engine`, `safety`, `bus`, `storage`,
  `orchestration`, `services`, `tools`, `ui`, `workflows`).
- Never import anything from `source_MIT/` (reference material only; see
  `docs/THIRD_PARTY_NOTICES.md` rule 4).
- The Tkinter client (`main.py` + `src/ui/`) is a *client*; business rules
  belong to the API/services layer.

### 1.2 Python style

- Target Python ≥ 3.10; the API image runs 3.12, dev machines 3.14.
- Type hints on every public function signature; `from __future__ import
  annotations` at the top of modules using `X | Y` unions.
- Module docstring first: say what the module does and which proposal
  item/phase introduced it; cite adapted upstream sources in the docstring
  (attribution requirement).
- Constants `UPPER_SNAKE`, functions/variables `snake_case`, classes
  `CamelCase`, dataclasses preferred over bare dicts at module boundaries.
- Stdlib-first: the Tkinter client must stay dependency-free; API
  dependencies go to `requirements-api.txt` with a bounded range until
  Phase 9 pins them.
- Fail closed in safety paths (policy, sandbox): an unavailable dependency
  denies, never allows.
- Every deferred feature is labelled `STRETCH` in code and docs and must not
  be required for a demo.
- No secrets in code or logs; read them from the environment.

### 1.3 Quality commands (must pass before every commit)

```bash
ruff format --check . && ruff check .   # format + lint
mypy src                                # types
python -m pytest tests -q               # tests
```

## 2. Branching conventions

- `main` (this checkout: `local_commit`) is always green; every commit must
  leave lint, types and tests passing.
- Feature branches are named `phase-N/short-kebab-description` (e.g.
  `phase-2/validator-endpoint`) or `fix/short-description`.
- Never `git push` unless explicitly asked; work is committed locally.
- No force-push, no history rewrites, no `git reset --hard` of shared
  commits. Backups use the `backup_local_commit_*` tag/branch pattern already
  present in this repo.
- One phase = one series of commits; do not start phase N+1 while a phase-N
  gate is open (proposal §15 rule).

## 3. Commit conventions

Conventional Commits, with the proposal item number appended:

```text
<type>(<scope>): <imperative summary> [<item-number>]

<body: why this change, what acceptance criterion it satisfies>

🤖 Generated with Codebuff
Co-Authored-By: Codebuff <noreply@codebuff.com>
```

- `type` ∈ `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `perf`,
  `ci`, `build`, `security`, `revert`.
- `scope` names the area: `app`, `engine`, `dsl`, `validator`, `executor`,
  `safety`, `bus`, `storage`, `planner`, `memory`, `sandbox`, `ui`, `tests`,
  `ci`, `docs`, `phaseN`.
- The `[N]` suffix **must** reference the proposal TODO item it closes, so
  commit history maps 1:1 onto `docs/issues/BACKLOG.md`.
- One logical step per commit; commit the smallest coherent change
  (proposal TODO step granularity — e.g. 9+ commits per phase for Phase 0).
- Never commit `.env`, secrets, `*.db`, `__pycache__`, `.coverage`, or the
  proposal drafts (`*tmp` is gitignored).

## 4. Review conventions

Self-review checklist before each commit (PR checklist when a remote
workflow exists):

1. Does the commit close exactly one TODO item (`[N]` present)?
2. Do the acceptance criteria of that item actually hold (run the relevant
   command, not just the build)?
3. Are new public functions typed and documented?
4. Is there at least one test for new logic (unit) or a justified reason why
   not?
5. Any new external dependency recorded in `docs/THIRD_PARTY_NOTICES.md`?
6. Anything deferred labelled `STRETCH`?
7. No secrets, no absolute local paths, no `source_MIT` imports.
8. Status mirror (`docs/IMPLEMENTATION_STATUS.md`) updated when a phase
   state changes.

## 5. Test conventions

- Location: `tests/unit/`, `tests/integration/`, `tests/acceptance/` —
  mirrors proposal §8.2; fixtures live next to the code they exercise
  (`src/engine/validator/fixtures/`).
- Names: `test_<unit>_<behavior>.py` with `test_*` functions/classes;
  `unittest` and `pytest` styles are both allowed (pytest runs both).
- Unit tests are hermetic: no network, no Redis/PostgreSQL requirement, no
  sleeping longer than ~1s; deterministic inputs only.
- Integration tests may need `docker compose up db redis` and must skip
  cleanly with an explicit reason when the service is absent.
- Safety regressions get adversarial tests (path traversal, dangerous
  fragments, duplicate events, tampered audit chain).

## 6. Documentation conventions

- Docs live in `docs/` and are English, with proposal section references
  (`proposal §10.3`).
- `docs/IMPLEMENTATION_STATUS.md` is the tracked mirror of phase/OK state;
  the proposal file (`SE_Proposal_v4.tmp`) is the human source of truth.
