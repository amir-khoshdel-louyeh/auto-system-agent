-- Phase 2 / migration 002: audit tables (ER proposal §5.1: A3 + A7).
-- Portable subset (SQLite + PostgreSQL) like 001: TEXT ids generated in
-- Python, INTEGER as 0/1 boolean, ISO-8601 TEXT timestamps. No SERIAL, no
-- extensions. Alembic wiring arrives with the SQLAlchemy phase.
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    version INTEGER NOT NULL,
    dsl_text TEXT NOT NULL,
    dsl_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workflow_id, version)
);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow
    ON workflow_versions (workflow_id);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    triggered_by TEXT NOT NULL DEFAULT 'manual',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_executions_workflow
    ON executions (workflow_id);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(id),
    node_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    decided_by TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_approvals_execution
    ON approvals (execution_id);

-- Append-only audit log with hash chain (A3): every run + approval + step
-- event links to the previous hash; tampering breaks `verify_chain`.
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_events_execution
    ON audit_events (execution_id, seq);
