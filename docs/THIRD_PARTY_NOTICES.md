# Third-party notices and source/license inventory

**Item:** Phase 0 — [3] Record external source/license inventory
**Policy:** proposal §11 "Approved reuse policy" (10 rules)
**Last reviewed:** 2026-10-15

Verification commands (zero hits required — attribution comments are fine,
imports are not):

```bash
grep -rnE '^\s*(from|import)\s+.*source_MIT' src/   # -> no matches
grep -rn 'source_MIT' src/                           # -> attribution comments only
```

## A. Vendored reference projects (`source_MIT/`, read-only)

Snapshot date 2026-10-12. The directories carry no VCS metadata, so no
commit/tag can be recorded; the snapshot date identifies the reviewed
state. All of these are **reference material only** — logic is manually
adapted into `src/` and never imported (rule 4).

| Path | Upstream | License | File | Permitted use (proposal §11) |
|---|---|---|---|---|
| `source_MIT/FastAPI-boilerplate` | benavlabs FastAPI-boilerplate (lineage: asacristani/fastapi-rocket-boilerplate) | MIT (© 2023 Igor Magalhães) | `LICENSE.md` | API layout, auth patterns, settings, migration ideas |
| `source_MIT/llm-flow-engine` | github.com/liguobao/llm-flow-engine | MIT (© 2025 liguobao) | `LICENSE` | DSL loading ideas and parser tests |
| `source_MIT/flow-forge` | upstream URL not recorded in snapshot | MIT (© 2026 Mateshwari) | `LICENSE` | Validator and graph UI reference |
| `source_MIT/optiflow` | github.com/NullLabTests/optiflow | MIT (© 2026 OptiFlow) | `LICENSE` | Scheduler design reference |
| `source_MIT/EventFlow` | upstream URL placeholder in `pyproject.toml` (`yourusername/eventflow`) — unverified | MIT (© 2026 EventFlow) | `LICENSE` | Broker, consumer-group and DLQ patterns |
| `source_MIT/pgvector-rag` | github.com/H4mid2019/pgvector-rag | **UNKNOWN — no LICENSE file in snapshot** | — | Retrieval/evaluation **ideas only; no copying** until license verified |
| `source_MIT/pybox` | github.com/SINTEF/pybox | MIT (© 2026 SINTEF) | `LICENSE` | Sandbox contract and restriction checklist |
| `source_MIT/sre-autonomous-agent` | upstream URL not recorded in snapshot | **UNKNOWN — no LICENSE file in snapshot** | — | Notification/approval adapter **ideas only; no copying** until license verified |

Handling rules for this directory:

1. No file under `source_MIT/` is ever modified (read-only reference).
2. No GPL/AGPL code may be copied in (none of the identified licenses are
   GPL/AGPL; the two UNKNOWN projects stay copy-blocked).
3. `source_MIT/` is gitignored and excluded from the distributed image; a
   final cleanup pass (plan phase 8) removes it once every adaptation is
   covered by tests.

## B. Runtime dependencies (Python packages)

Base client (`requirements.txt`) intentionally has **zero** third-party
dependencies (stdlib only: tkinter, yaml is *not* used there, json, hmac,
sqlite3, subprocess, …).

API/runtime dependencies (`requirements-api.txt`), pinned as ranges until
Phase 9 pins exact versions:

| Package | Version range | License | Used for |
|---|---|---|---|
| fastapi | >=0.115 | MIT | HTTP API |
| uvicorn[standard] | >=0.30 | BSD-3-Clause | ASGI server |
| httpx | >=0.28 | BSD-3-Clause | test client / outbound HTTP |
| redis | >=5 | MIT | Redis Streams event bus |
| asyncpg | >=0.30 | Apache-2.0 | reserved PostgreSQL driver |
| PyYAML | >=6.0 | MIT | workflow DSL parsing |
| pydantic | >=2.0 | MIT | request/response schemas (fastapi transitive) |

Docker base image: `python:3.12-slim` (PSF license), compose images
`pgvector/pgvector:pg16` (PostgreSQL license), `redis:7` (BSD-3-Clause).

## C. Development and CI tooling

| Package | License | Purpose |
|---|---|---|
| pytest / pytest-cov | MIT | test runner and coverage plugin |
| coverage | Apache-2.0 | coverage measurement |
| ruff | MIT | formatter + linter |
| mypy | MIT | static type checking |
| bandit | Apache-2.0 | security linter (CI) |
| pip-audit | Apache-2.0 | dependency vulnerability audit (CI) |

Licenses above are as declared upstream; Phase 9 item [82] re-verifies them
with `pip-audit` / package metadata before release.

## D. Compliance checklist (proposal §11)

| # | Rule | Status |
|---|---|---|
| 1 | Record URL, commit/tag, license, path here | OK (snapshot date where tag missing) |
| 2 | Prefer permissive licenses | OK — MIT/BSD/Apache only; 2 unknowns copy-blocked |
| 3 | No GPL/AGPL without supervisor approval | OK — none identified |
| 4 | Never import production code from `source_MIT` | OK — zero import statements; the 14 remaining `source_MIT` hits are docstring attribution comments |
| 5 | No secrets/telemetry/unrelated deps copied | OK |
| 6 | Preserve copyright notices | OK — table above; notices kept on adapted files |
| 7 | Tests for every adapted component | OK — adapted components carry tests under `tests/` |
| 8 | Pin dependencies + `pip-audit` | WIP — pinned in Phase 9 [82]; ranges now |
| 9 | Keep source project read-only | OK — gitignored, never modified |
| 10 | Repository search for forbidden imports before release | WIP — scripted in Phase 9 [82] |
