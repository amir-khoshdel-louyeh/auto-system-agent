-- Phase 1 / migration 004: teams, members and workspaces (item [14], §9).
-- Idempotent (CREATE TABLE IF NOT EXISTS) so the apply-all runner of item
-- [15] can re-run it safely; portable subset: TEXT ids generated in
-- Python, ISO-8601 TEXT timestamps, no SERIAL/extensions.
--
-- Roles are workspace/team scoped: a member row overrides the global
-- user_roles row for that container (see db.effective_role).
-- No FOREIGN KEY clauses: SQLite enforces them only when the pragma is on
-- and the Postgres port applies the same files; referential integrity is
-- enforced in the repository layer (item [16]).
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'VIEWER',
    joined_at TEXT NOT NULL,
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    team_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'VIEWER',
    joined_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
