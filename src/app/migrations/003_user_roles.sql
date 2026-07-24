-- Phase 1 / migration 003: per-user RBAC role (item [12]).
-- Idempotent under the current apply-all runner (only CREATE TABLE IF NOT
-- EXISTS), so re-running init_db stays clean; the versioned, checksummed
-- migration runner of item [15] adopts this file with the rest.
-- A user with no row here is a VIEWER (least privilege default).
CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'VIEWER',
    granted_at TEXT NOT NULL,
    granted_by TEXT
);
