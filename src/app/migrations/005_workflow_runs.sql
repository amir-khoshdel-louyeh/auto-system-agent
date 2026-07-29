-- Phase 3 / migration 005: workflow triggers, run steps and approval requests
-- (items [27], [28], [35]). Additive only: the checksummed runner of item
-- [15] never re-applies a file, so ALTER ... ADD COLUMN is safe on every
-- target (SQLite + PostgreSQL) and pre-existing rows keep their defaults.
-- Portable subset: TEXT ids/timestamps, INTEGER as int/boolean, no SERIAL.
ALTER TABLE workflows ADD COLUMN workspace_id TEXT NOT NULL DEFAULT '';
ALTER TABLE workflows ADD COLUMN created_by TEXT NOT NULL DEFAULT '';

ALTER TABLE workflow_versions ADD COLUMN published_by TEXT NOT NULL DEFAULT '';
ALTER TABLE workflow_versions ADD COLUMN published_at TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS workflow_triggers (
    workflow_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (workflow_id, kind, value)
);

-- One row per node per attempt: the durable trace the executor writes before
-- a step is considered done (FR-10, FR-11; at-least-once with idempotency).
CREATE TABLE IF NOT EXISTS execution_steps (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(id),
    node_id TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    error TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE (execution_id, node_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_execution_steps_execution
    ON execution_steps (execution_id, node_id);

-- An approval request carries the quorum and deadline; decisions are the
-- existing `approvals` rows, so quorum is a count over them (item [35]).
CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(id),
    node_id TEXT NOT NULL,
    quorum INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'PENDING',
    expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (execution_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_status
    ON approval_requests (status, expires_at);
