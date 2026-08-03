-- Phase 7 / migration 007: reuse-hit and no-generation-cost accounting.
--
-- One row per *planning request that consulted memory*, recorded after the
-- retrieval ran. The raw query is never stored (it may contain PII); only its
-- SHA-256 digest and length are kept, which is enough to deduplicate counting
-- without turning the metrics table into a second copy of user input.
--
-- `hit = 1` means a PUBLIC, in-filter plan was returned, so the request was
-- answered from memory and no planner generation call was needed.
--
-- Idempotent (CREATE TABLE IF NOT EXISTS) for the apply-all runner of item 15.

CREATE TABLE IF NOT EXISTS memory_retrievals (
    id TEXT PRIMARY KEY,
    query_hash TEXT NOT NULL,
    query_length INTEGER NOT NULL DEFAULT 0,
    hit INTEGER NOT NULL DEFAULT 0,
    hit_id TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_retrievals_created ON memory_retrievals (created_at);
CREATE INDEX IF NOT EXISTS idx_memory_retrievals_hit ON memory_retrievals (hit);
