-- Migration 0004: notes_history table.
--
-- Append-only edit history for jobs.user_notes. The /notes route handler
-- writes here only on the `blur` HTMX event (not on keyup-debounce) to
-- avoid flooding the table with mid-edit keystrokes.
--
-- Issue: #696 (#662 F7)

CREATE TABLE IF NOT EXISTS notes_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    author TEXT NOT NULL DEFAULT 'operator'
);

CREATE INDEX IF NOT EXISTS idx_notes_history_job_updated
    ON notes_history(job_id, updated_at DESC);
