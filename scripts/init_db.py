#!/usr/bin/env python3
# scripts/init_db.py
import sqlite3
import sys

from findajob.paths import BASE

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else f"{BASE}/data/pipeline.db"

conn = sqlite3.connect(DB_PATH, timeout=30)

# Legacy-stack migrations: CREATE TABLE IF NOT EXISTS is a no-op when the
# table already exists, which means new columns declared below never land
# on upgraded DBs. Handle each additive column here via ALTER TABLE so
# subsequent index-creation statements (e.g. idx_jobs_loose_fingerprint)
# don't reference a missing column and crash the entrypoint.
_existing_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
if "jobs" in _existing_tables:
    _jobs_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "loose_fingerprint" not in _jobs_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN loose_fingerprint TEXT")
        conn.commit()
    if "synthetic" not in _jobs_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "speculative_briefing_folder" not in _jobs_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN speculative_briefing_folder TEXT")
        conn.commit()

# v0.20.0 migration: cost numbers now come from response.usage.cost
# (written natively by findajob.llm.openrouter). The legacy calibration
# table is dropped on existing stacks; no-op on fresh init.
if "cost_calibration" in _existing_tables:
    conn.execute("DROP INDEX IF EXISTS idx_cost_calibration_polled_at")
    conn.execute("DROP TABLE cost_calibration")
    conn.commit()

conn.executescript("""
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    loose_fingerprint TEXT,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT NOT NULL,
    raw_jd_text TEXT,

    relevance_score INTEGER CHECK(relevance_score BETWEEN 1 AND 10),
    interview_likelihood INTEGER CHECK(interview_likelihood BETWEEN 1 AND 10),
    strengths_alignment TEXT,
    industry_sector TEXT,
    comp_estimate TEXT DEFAULT '',
    ai_notes TEXT,
    score_status TEXT CHECK(score_status IN ('scored', 'manual_review', 'needs_info')),
    score_flag_reason TEXT,
    remote_status TEXT DEFAULT 'Unknown',

    network_depth INTEGER DEFAULT 0,
    known_contacts TEXT DEFAULT '',
    stage TEXT DEFAULT 'discovered' CHECK(stage IN (
        'discovered', 'enriched', 'scored', 'manual_review',
        'prep_in_progress', 'materials_drafted', 'waitlisted', 'applied',
        'response_received', 'interview', 'offer', 'rejected', 'withdrawn'
    )),
    stage_updated TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN (
        'active', 'manual_review', 'skipped', 'applied',
        'rejected', 'interviewing', 'offer'
    )),
    apply_flag INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '',
    prep_folder_path TEXT,
    gdrive_folder_url TEXT,
    fit_score REAL,
    probability_score REAL,
    user_notes TEXT DEFAULT '',

    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    dupe_of TEXT DEFAULT '',
    synthetic INTEGER NOT NULL DEFAULT 0,
    speculative_briefing_folder TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_loose_fingerprint ON jobs(loose_fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_stage ON jobs(stage);
CREATE INDEX IF NOT EXISTS idx_jobs_apply_flag ON jobs(apply_flag);
CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now')),
    changed_by TEXT DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_audit_job_id ON audit_log(job_id);

CREATE TABLE IF NOT EXISTS cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    operation TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER,
    success INTEGER DEFAULT 1,
    error_message TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    logged_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cost_log_job_id ON cost_log(job_id);

CREATE TABLE IF NOT EXISTS feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    relevance_score INTEGER,
    reject_reason TEXT NOT NULL,
    jd_excerpt TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    canonical_fingerprint TEXT NOT NULL,
    duplicate_job_id TEXT NOT NULL,
    detected_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (canonical_fingerprint, duplicate_job_id)
);

CREATE TABLE IF NOT EXISTS speculative_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    hint TEXT,
    personal_notes TEXT,
    status TEXT NOT NULL DEFAULT 'researching' CHECK(status IN (
        'researching', 'ready_for_review', 'approved', 'trashed', 'failed'
    )),
    error_message TEXT,
    briefing_md TEXT,
    role_cards_json TEXT,
    briefing_folder TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    research_completed_at TEXT,
    approved_at TEXT,
    approved_role_count INTEGER,
    briefing_prompt_version TEXT,
    synth_prompt_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_speculative_status ON speculative_requests(status);
CREATE INDEX IF NOT EXISTS idx_speculative_company_submitted ON speculative_requests(company, submitted_at);

CREATE TABLE IF NOT EXISTS onboarding_sessions (
    id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL,
    captured_blocks_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    last_turn_at TEXT NOT NULL,
    completed_at TEXT,
    error_state TEXT,
    -- Per-tester credentials (#339). Older DBs back-fill via session_store.migrate_schema().
    tester_openrouter_key TEXT DEFAULT NULL,
    tester_rapidapi_key   TEXT DEFAULT NULL,
    -- Running interview cost (2026-05-02). Sum of OpenRouter's per-turn `usage.cost`.
    cumulative_cost_usd REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_onboarding_sessions_completed ON onboarding_sessions(completed_at);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'default',
    tags TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'sent' CHECK(delivery_status IN (
        'sent', 'failed', 'in_app_only'
    )),
    delivery_error TEXT,
    cta_url TEXT,
    read_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_sent_at ON notifications(sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(read_at) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_kind ON notifications(kind);
""")
conn.close()
print("Database initialized:", DB_PATH)
