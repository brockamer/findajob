-- Migration 0005: view_prefs table.
--
-- Per-tab persistence of the filter/sort/cols querystring. Auto-saved
-- on every /rows GET; cleared by POST /board/{tab}/reset-view. The
-- query_string is reconstructed from ParsedFilters (allowlisted) so
-- unrelated params like ?density= are never persisted.
--
-- Issue: #277

CREATE TABLE IF NOT EXISTS view_prefs (
    tab TEXT PRIMARY KEY CHECK (tab IN (
        'dashboard','applied','review','waitlist',
        'rejected','not_selected','archive'
    )),
    query_string TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
