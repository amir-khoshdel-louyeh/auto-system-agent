# Proposal Approval and MVP Scope Freeze

**Item:** Phase 0 — [1] Approve this proposal and freeze MVP scope
**Proposal:** `SE_Proposal_v4.tmp` — "Team Automation Hub", Version 4.0 (implementation-ready)
**Approved:** 2026-10-15
**Approvers:** Amir Khoshdel Louyeh (557530), Alireza Refahifard (557192)
**Status:** APPROVED — MVP scope frozen

---

## 1. What was approved

The scope described in `SE_Proposal_v4.tmp` sections 1–14 is approved as the
MVP contract for this repository, with the phased implementation TODO in
section 15 as the work breakdown. Each phase carries a gate; **a later phase
must not hide an incomplete earlier phase.**

## 2. Frozen MVP scope (binding)

| Dimension | Frozen value |
|---|---|
| Deployable unit | one FastAPI application (service-oriented monolith) |
| System database | PostgreSQL (`compose` service `db`, pgvector image) |
| Event bus | Redis Streams (`compose` service `redis`) |
| Workflow node types | exactly 5: `shell`, `gui_click`, `gui_type`, `approval`, `custom` |
| Trigger types | exactly 3: manual API, `file.arrived`, cron/schedule |
| Shell/file automation | restricted to an approved workspace; allow-list + dangerous-fragment detection |
| AuthN / AuthZ | password hash + short-lived JWT; RBAC roles `OWNER`, `OPERATOR`, `APPROVER`, `VIEWER` |
| Approval | quorum-gated with expiry, immutable hash-chain audit |
| Planner | offline-first rules/NLP; optional LLM adapter behind a typed interface, never authoritative |
| Demo scenarios | W1 (governed workspace onboarding), W2 (versioned event-driven workflow), W3 optional |
| Quality | ≥80% coverage on planner, validator, policy, scheduler, audit; CI quality gates |

## 3. Explicitly deferred (STRETCH — never an MVP gate)

OAuth (Google/GitHub), production Slack/Telegram integrations, full
drag-and-drop visual IDE, VLM screen grounding, macOS/Wayland GUI
automation, S3/MinIO artifacts, Kubernetes/Kafka, public template
marketplace, EigenTrust reputation and multi-tenant billing.

Every deferred feature must be labelled `STRETCH` in code and docs
(see `docs/CONVENTIONS.md`).

## 4. Change control

After this approval:

1. Adding an MVP feature requires a new proposal version and re-approval by
   both team members, recorded in this file.
2. Moving a feature to STRETCH requires only a note here plus the matching
   `STRETCH` label in code/docs.
3. The phase gates of section 15 are the only acceptance criteria for
   marking a phase **OK**; progress is tracked in
   `docs/IMPLEMENTATION_STATUS.md`.

## 5. Gate register

| Phase | Gate |
|---|---|
| 0 | a new developer can install dependencies, run tests and start the health endpoint |
| 1 | unauthorized users cannot access protected operations; a clean database migrates |
| 2 | invalid workflows are never persisted or deployed |
| 3 | a three-node workflow executes correctly; dangerous commands cannot bypass policy |
| 4 | W2 runs through the bus; duplicate events cause no duplicate side effect |
| 5 | W1 and W2 provide complete, inspectable evidence |
| 6 | planner output always passes the same validator/policy path as a hand-authored workflow |
| 7 | a safe plan can be reused; a failed/poisoned plan cannot be auto-reused |
| 8 | a plugin or untrusted plan cannot write outside its workspace |
| 9 | all NFR targets have measured evidence, not only narrative claims |
| 10 | a reviewer can connect every major requirement to code, test and demo evidence |
