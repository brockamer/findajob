-- Migration 0011: filter_proposals table.
--
-- Rejection-derived prefilter rule proposals. analyze_feedback mines title
-- n-grams from title-signal rejections into proposed_regex candidates; this
-- table queues them for operator approve/skip/revert. On apply, the regex is
-- written to prefilter_rules.yaml (hard_rejects.auto_added) and existing
-- matching jobs are re-scored to 1. affected_jobs captures prior scores so a
-- revert can restore them.
--
-- Spec: docs/superpowers/specs/2026-06-10-rejection-driven-filter-proposals-design.md
-- Issue: #1055

CREATE TABLE IF NOT EXISTS filter_proposals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern               TEXT NOT NULL,
    pattern_norm          TEXT NOT NULL,
    category              TEXT NOT NULL DEFAULT 'auto_added',
    source_reason         TEXT,
    support_count         INTEGER DEFAULT 0,
    support_sample        TEXT,
    preview_count         INTEGER DEFAULT 0,
    preview_sample        TEXT,
    preview_danger_count  INTEGER DEFAULT 0,
    status                TEXT NOT NULL DEFAULT 'pending',
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at            TEXT,
    config_change_id      INTEGER,
    affected_jobs         TEXT
);

CREATE INDEX IF NOT EXISTS idx_filter_proposals_status
    ON filter_proposals (status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_filter_proposals_norm
    ON filter_proposals (pattern_norm);
