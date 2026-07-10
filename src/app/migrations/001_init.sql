-- Phase 0 / migration 001: users table.
-- Portable subset (SQLite + PostgreSQL): TEXT ids generated in Python,
-- INTEGER used as 0/1 boolean, ISO-8601 TEXT timestamps. No SERIAL,
-- no extensions, so the same file seeds local sqlite and reviews cleanly
-- for the Postgres (+pgvector) target. Alembic wiring arrives with the
-- SQLAlchemy phase; until then db.py applies this file via executescript.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
