-- Phase 7 / migration 006: shared memory, consent and reputation (proposal §9).
--
-- Invariants enforced here rather than in application code:
--   * a memory item needs a consenting subject to exist at all (the store
--     refuses to persist without one);
--   * one item per canonical-plan fingerprint (idempotent reuse);
--   * the quarantine state is the default, so a new plan is never reusable
--     until it has been exercised (`QUARANTINED` -> `SANDBOX` -> `PUBLIC`).
--
-- Idempotent (CREATE TABLE IF NOT EXISTS) for the apply-all runner of item 15.

CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    scope TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT '',
    UNIQUE (subject, scope)
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT '',
    canonical_plan TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    embedding TEXT NOT NULL DEFAULT '',
    pii_findings INTEGER NOT NULL DEFAULT 0,
    risk TEXT NOT NULL DEFAULT 'LOW',
    os_name TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'QUARANTINED',
    reputation REAL NOT NULL DEFAULT 20,
    reuse_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    rating_sum REAL NOT NULL DEFAULT 0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_state ON memory_items (state, reputation);

CREATE TABLE IF NOT EXISTS memory_events (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_events_item ON memory_events (memory_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    uri TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
