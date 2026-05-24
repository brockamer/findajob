-- Migration 0007: tuning loop Phase 1
-- Adds config_changes and recall_audit tables, and company_tier + scored_by
-- columns to jobs. All DDL is idempotent (CREATE IF NOT EXISTS / ADD COLUMN
-- with no-op on duplicate-column error handled by the migration runner).

CREATE TABLE IF NOT EXISTS config_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lever        TEXT NOT NULL,
    changed_at   TEXT DEFAULT (datetime('now')),
    changed_by   TEXT DEFAULT 'manual',
    change_summary TEXT,
    content_hash TEXT,
    diff_summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_config_changes_lever_time
    ON config_changes (lever, changed_at);

CREATE TABLE IF NOT EXISTS recall_audit (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id             TEXT NOT NULL,
    audited_at         TEXT DEFAULT (datetime('now')),
    original_score     INTEGER,
    original_scored_by TEXT,
    auditor_model      TEXT NOT NULL,
    audited_score      INTEGER,
    upgraded           INTEGER DEFAULT 0,
    audit_notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_recall_audit_time
    ON recall_audit (audited_at);

ALTER TABLE jobs ADD COLUMN company_tier TEXT DEFAULT 'unknown';
ALTER TABLE jobs ADD COLUMN scored_by TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_jobs_company_tier ON jobs (company_tier);
CREATE INDEX IF NOT EXISTS idx_jobs_scored_by ON jobs (scored_by);
