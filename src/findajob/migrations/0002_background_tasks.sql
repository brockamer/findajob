-- 0002_background_tasks.sql — observability for detached subprocess work.
--
-- Each row records one prep / interview_prep / speculative_research run.
-- Subprocesses write back ``status='succeeded'`` or ``'failed'`` on exit;
-- watchdog reaps any row stuck in ``running`` past its kind's timeout.
--
-- ``job_id`` is overloaded by ``kind``:
--   - ``prep`` / ``interview_prep`` → ``jobs.id`` (TEXT uuid)
--   - ``speculative_research`` → ``speculative_requests.id`` stringified
--
-- Idempotent — ``CREATE TABLE IF NOT EXISTS`` so re-running 0002 against
-- an already-migrated stack is a no-op (the M5 runner enforces "version >
-- current" gating, but this is belt-and-suspenders).

CREATE TABLE IF NOT EXISTS background_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('prep', 'prep_phase_b', 'interview_prep', 'speculative_research', 'podcast', 'study_guide', 'flashcards')),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'succeeded', 'failed')),
    error_message TEXT,
    pid INTEGER
);

-- Lookup by subject (the (job_id, kind) pair) — for status pages
-- finding "the most recent task for this row".
CREATE INDEX IF NOT EXISTS idx_background_tasks_job_id ON background_tasks(job_id);

-- Watchdog query: rows in 'running' state past their kind's timeout.
-- Composite (status, kind) so the index covers the WHERE clause without
-- a table scan; started_at is filtered after.
CREATE INDEX IF NOT EXISTS idx_background_tasks_status_kind ON background_tasks(status, kind);
