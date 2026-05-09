-- Migration 0003: rejection_suggestions table.
--
-- Tracks Gmail-detected company-rejection emails matched against active
-- applications. Operator confirms one-click → handle_not_selected fires.
-- Never auto-flips; always operator-in-loop.
--
-- Spec: docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md §4.3
-- Issue: #362

CREATE TABLE IF NOT EXISTS rejection_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id TEXT NOT NULL UNIQUE,
    received_at TEXT NOT NULL,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_excerpt TEXT NOT NULL,
    extracted_company TEXT,
    extracted_role TEXT,
    matched_job_id TEXT,
    match_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    suggested_reason TEXT NOT NULL,
    user_action TEXT NOT NULL DEFAULT 'pending',
    user_action_at TEXT,
    user_chose_job_id TEXT
);

CREATE INDEX IF NOT EXISTS rejection_suggestions_user_action
    ON rejection_suggestions(user_action);

CREATE INDEX IF NOT EXISTS rejection_suggestions_matched_job
    ON rejection_suggestions(matched_job_id);
