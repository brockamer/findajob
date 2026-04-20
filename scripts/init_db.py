#!/usr/bin/env python3
# scripts/init_db.py
import sqlite3

from findajob.paths import BASE

DB_PATH = f"{BASE}/data/pipeline.db"

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.executescript("""
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
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
    dupe_of TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint);
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
""")
conn.close()
print("Database initialized:", DB_PATH)
