"""Tests for findajob.speculative.approver — writes jobs rows on Approve."""

from __future__ import annotations

import json
import sqlite3

import pytest

from findajob.speculative.approver import approve_request

JOBS_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT NOT NULL,
    raw_jd_text TEXT,
    relevance_score INTEGER,
    interview_likelihood INTEGER,
    strengths_alignment TEXT,
    industry_sector TEXT,
    comp_estimate TEXT DEFAULT '',
    ai_notes TEXT,
    score_status TEXT,
    score_flag_reason TEXT,
    remote_status TEXT DEFAULT 'Unknown',
    network_depth INTEGER DEFAULT 0,
    known_contacts TEXT DEFAULT '',
    stage TEXT DEFAULT 'discovered',
    stage_updated TEXT,
    status TEXT DEFAULT 'active',
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
CREATE TABLE speculative_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    hint TEXT,
    personal_notes TEXT,
    status TEXT NOT NULL DEFAULT 'researching',
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
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now')),
    changed_by TEXT DEFAULT 'system'
);
"""


def _seed_ready(conn: sqlite3.Connection, n_cards: int = 3) -> int:
    cards = [
        {
            "title": f"Role {i}",
            "description": "D",
            "why_this_fits_candidate": "W",
            "likely_team_or_org": "T",
            "suggested_contact_type": "recruiter",
        }
        for i in range(n_cards)
    ]
    cur = conn.execute(
        """INSERT INTO speculative_requests
           (company, status, briefing_md, role_cards_json, briefing_folder)
           VALUES (?, 'ready_for_review', ?, ?, ?)""",
        ("PSIQuantum", "# briefing\n", json.dumps(cards), "PSIQuantum_SPECULATIVE_2026-04-28_140000"),
    )
    conn.commit()
    return cur.lastrowid


def test_approve_writes_one_job_per_kept_card():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(JOBS_SCHEMA)
    req_id = _seed_ready(conn, n_cards=3)

    approve_request(conn, request_id=req_id, kept_indices=[0, 2])

    jobs = conn.execute("SELECT * FROM jobs ORDER BY title").fetchall()
    assert len(jobs) == 2
    assert all(j["synthetic"] == 1 for j in jobs)
    assert all(j["source"] == "web_speculative" for j in jobs)
    assert all(j["title"].startswith("[SPEC] ") for j in jobs)
    assert all(j["stage"] == "scored" for j in jobs)
    titles = sorted(j["title"] for j in jobs)
    assert titles == ["[SPEC] Role 0", "[SPEC] Role 2"]


def test_approve_updates_request_status_and_count():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(JOBS_SCHEMA)
    req_id = _seed_ready(conn, n_cards=4)

    approve_request(conn, request_id=req_id, kept_indices=[1, 3])

    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "approved"
    assert row["approved_role_count"] == 2
    assert row["approved_at"] is not None


def test_approve_with_zero_kept_indices_marks_trashed():
    """Approving with all cards dropped means 'I changed my mind' — equivalent to trash."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(JOBS_SCHEMA)
    req_id = _seed_ready(conn, n_cards=3)

    approve_request(conn, request_id=req_id, kept_indices=[])

    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "trashed"
    job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert job_count == 0


def test_approve_rejects_non_ready_status():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(JOBS_SCHEMA)
    cur = conn.execute("INSERT INTO speculative_requests (company, status) VALUES (?, 'researching')", ("X",))
    req_id = cur.lastrowid

    with pytest.raises(ValueError, match="status"):
        approve_request(conn, request_id=req_id, kept_indices=[0])


def test_approve_propagates_briefing_folder_to_jobs():
    """#320: approver copies speculative_requests.briefing_folder onto each new
    jobs row's speculative_briefing_folder column. prep_application.py reads
    that column to reuse the deep-research briefing instead of regenerating
    via briefing_writer.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(JOBS_SCHEMA)
    req_id = _seed_ready(conn, n_cards=2)

    approve_request(conn, request_id=req_id, kept_indices=[0, 1])

    jobs = conn.execute("SELECT * FROM jobs ORDER BY title").fetchall()
    assert len(jobs) == 2
    folder = "PSIQuantum_SPECULATIVE_2026-04-28_140000"
    for j in jobs:
        assert j["speculative_briefing_folder"] == folder, (
            f"expected jobs.speculative_briefing_folder={folder!r} on every approved synthetic row"
        )
