"""Unit tests for :func:`findajob.ingest.ingest_manual_job`.

The helper is the shared entry point for manual ingest (web form #62 + the
legacy Google-Form script). Tests cover fresh inserts, all three dedup
tiers (strict / url / loose), raw_jd_text storage, and the optional
generate_folder subprocess launch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob import ingest as ingest_mod
from findajob import utils as findajob_utils
from findajob.cleaning import clean_company, clean_title, fingerprint, loose_fingerprint

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    loose_fingerprint TEXT,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT NOT NULL,
    raw_jd_text TEXT,
    remote_status TEXT DEFAULT 'Unknown',
    known_contacts TEXT DEFAULT '',
    ai_notes TEXT,
    relevance_score INTEGER,
    stage TEXT DEFAULT 'discovered',
    apply_flag INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '',
    prep_folder_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    dupe_of TEXT DEFAULT '',
    synthetic INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    relevance_score INTEGER,
    reject_reason TEXT NOT NULL,
    jd_excerpt TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture()
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    # Redirect pipeline.jsonl to tmp so log_event() calls during ingest
    # don't write to the real logs/ directory.
    monkeypatch.setattr(findajob_utils, "LOG_PATH", str(tmp_path / "events.jsonl"))
    db_path = tmp_path / "pipeline.db"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


@pytest.fixture()
def popen_calls(monkeypatch) -> list[list[str]]:
    """Capture the Popen call that generate_folder=True makes — tests must
    not actually launch prep_application.py."""
    calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args, **_kw):
            calls.append(args)

    monkeypatch.setattr(ingest_mod.subprocess, "Popen", _FakePopen)
    return calls


def _submit(conn: sqlite3.Connection, **kwargs):
    defaults: dict = {
        "company": "Acme Data Centers",
        "title": "Senior Operations Engineer",
        "url": "https://boards.greenhouse.io/acme/jobs/42",
        "raw_jd_text": "Lead a team of ops engineers…",
        "location": "Menlo Park, CA",
        "source": "web_manual",
    }
    defaults.update(kwargs)
    return ingest_mod.ingest_manual_job(conn, **defaults)


def test_fresh_submission_inserts_row(conn: sqlite3.Connection, popen_calls):
    result = _submit(conn)
    assert result.status == "ingested"
    assert result.prep_launched is False
    assert popen_calls == []

    row = conn.execute("SELECT * FROM jobs WHERE id=?", (result.job_id,)).fetchone()
    assert row is not None
    assert row["company"] == "Acme Data Centers"
    assert row["title"] == "Senior Operations Engineer"
    assert row["source"] == "web_manual"
    assert row["stage"] == "scored"
    assert row["relevance_score"] == 8
    assert row["apply_flag"] == 0
    assert row["raw_jd_text"] == "Lead a team of ops engineers…"
    assert row["id"].startswith("web_manual-")
    assert row["loose_fingerprint"] is not None


def test_raw_jd_text_blank_stored_as_null(conn: sqlite3.Connection, popen_calls):
    result = _submit(conn, raw_jd_text="   ")
    row = conn.execute("SELECT raw_jd_text FROM jobs WHERE id=?", (result.job_id,)).fetchone()
    assert row["raw_jd_text"] is None


def test_clean_title_strips_nbsp_and_whitespace(conn: sqlite3.Connection, popen_calls):
    # NBSP (U+00A0) sneaks in via pasted job board titles; clean_title must strip it
    # so the web form produces the same fingerprint as an automated ingest would.
    result = _submit(conn, title="  Senior Operations  Engineer  ")
    row = conn.execute("SELECT title FROM jobs WHERE id=?", (result.job_id,)).fetchone()
    assert row["title"] == "Senior Operations Engineer"


def test_strict_fingerprint_duplicate(conn: sqlite3.Connection, popen_calls):
    first = _submit(conn)
    assert first.status == "ingested"

    second = _submit(conn)  # identical submission — existing row is scored, so resurfaced
    assert second.status == "resurfaced"
    assert second.job_id == first.job_id

    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert count == 1


def test_url_duplicate(conn: sqlite3.Connection, popen_calls):
    first = _submit(conn)
    # Same URL, different title/location → strict fingerprint misses, URL matches;
    # existing row is scored so returns resurfaced
    second = _submit(
        conn,
        title="Operations Manager",
        location="Remote",
    )
    assert second.status == "resurfaced"
    assert second.job_id == first.job_id


def test_tier2_loose_dedup_when_existing_has_coarse_location(conn: sqlite3.Connection, popen_calls):
    # Existing row has coarse location ("" / "US"); new row comes in with a
    # specific city for the same (company, title). Tier 2 should match —
    # this is the cross-source syndication case from #182 Bug C.
    first = _submit(conn, location="United States", url="https://greenhouse.io/a/1")
    second = _submit(conn, location="Barstow, TX", url="https://linkedin.com/jobs/999")
    assert second.status == "resurfaced"
    assert second.job_id == first.job_id


def test_tier2_loose_dedup_when_incoming_has_coarse_location(conn: sqlite3.Connection, popen_calls):
    _submit(conn, location="Barstow, TX", url="https://greenhouse.io/a/1")
    second = _submit(conn, location="", url="https://linkedin.com/jobs/999")
    assert second.status == "resurfaced"


def test_distinct_cities_do_not_collapse(conn: sqlite3.Connection, popen_calls):
    # Both rows have specific (non-coarse) locations — they are genuinely
    # distinct reqs (site manager in Barstow vs Prineville). Must NOT match
    # on Tier 2.
    first = _submit(conn, location="Barstow, TX", url="https://greenhouse.io/a/1")
    second = _submit(conn, location="Prineville, OR", url="https://greenhouse.io/a/2")
    assert first.status == "ingested"
    assert second.status == "ingested"
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert count == 2


def test_generate_folder_launches_prep(conn: sqlite3.Connection, popen_calls):
    result = _submit(conn, generate_folder=True)
    assert result.prep_launched is True
    assert len(popen_calls) == 1
    args = popen_calls[0]
    # [python, prep_application.py, company, title, url, job_id]
    assert args[1].endswith("/scripts/prep_application.py")
    assert args[2] == "Acme Data Centers"
    assert args[3] == "Senior Operations Engineer"
    assert args[5] == result.job_id


def test_source_label_threaded_through(conn: sqlite3.Connection, popen_calls):
    result = _submit(conn, source="manual_form")  # legacy script identifier
    row = conn.execute("SELECT source FROM jobs WHERE id=?", (result.job_id,)).fetchone()
    assert row["source"] == "manual_form"
    assert result.job_id.startswith("manual_form-")


def _insert_existing(
    conn: sqlite3.Connection,
    *,
    stage: str,
    score: int = 5,
    company: str = "Acme Data Centers",
    title: str = "Senior Operations Engineer",
    location: str = "United States",
    reject_reason: str = "",
    folder: str | None = None,
) -> sqlite3.Row:
    """Insert a pre-existing job at a given stage (imitates a row triage or a
    prior ingest created). Uses coarse location so loose-dedup fires on re-submit."""
    co = clean_company(company)
    ti = clean_title(title)
    fp = fingerprint(ti, co, location)
    lfp = loose_fingerprint(ti, co)
    job_id = f"triage-{fp}"
    conn.execute(
        """INSERT INTO jobs
           (id, fingerprint, loose_fingerprint, url, title, company, location, source,
            relevance_score, stage, apply_flag, reject_reason, prep_folder_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'triage', ?, ?, 0, ?, ?)""",
        (job_id, fp, lfp, f"https://example.com/{fp}", ti, co, location, score, stage, reject_reason, folder),
    )
    conn.commit()
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


class TestHandleDuplicate:
    """_handle_duplicate branch tests — each exercises one stage category."""

    def test_applied_stage_returns_already_applied(self, conn, popen_calls):
        _insert_existing(conn, stage="applied", score=8)
        result = _submit(conn, location="United States")
        assert result.status == "already_applied"
        assert result.existing_stage == "applied"
        # No new row
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    def test_interview_stage_returns_already_applied(self, conn, popen_calls):
        _insert_existing(conn, stage="interview", score=8)
        result = _submit(conn, location="United States")
        assert result.status == "already_applied"

    def test_offer_stage_returns_already_applied(self, conn, popen_calls):
        _insert_existing(conn, stage="offer", score=8)
        result = _submit(conn, location="United States")
        assert result.status == "already_applied"

    def test_withdrew_stage_returns_already_applied(self, conn, popen_calls):
        _insert_existing(conn, stage="withdrew", score=8)
        result = _submit(conn, location="United States")
        assert result.status == "already_applied"

    def test_not_selected_returns_not_selected_with_folder(self, conn, popen_calls, tmp_path):
        folder = str(tmp_path / "companies" / "_applied" / "Acme_Senior_2026-01-01_120000")
        _insert_existing(conn, stage="not_selected", score=8, folder=folder)
        result = _submit(conn, location="United States")
        assert result.status == "not_selected"
        assert result.existing_stage == "not_selected"
        assert result.prep_folder_path == folder
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

    def test_rejected_returns_resurfaced_and_updates_stage(self, conn, popen_calls):
        existing = _insert_existing(conn, stage="rejected", score=4, reject_reason="Low Fit Score")
        # Seed a feedback_log row that should be deleted
        conn.execute(
            "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason)"
            " VALUES (?, 'Senior Operations Engineer', 'Acme Data Centers', 4, 'Low Fit Score')",
            (existing["id"],),
        )
        conn.commit()
        result = _submit(conn, location="United States")
        assert result.status == "resurfaced"
        assert result.existing_stage == "rejected"
        row = conn.execute("SELECT stage, relevance_score, reject_reason FROM jobs").fetchone()
        assert row["stage"] == "scored"
        assert row["relevance_score"] == 8
        assert row["reject_reason"] == ""
        assert conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0] == 0

    def test_waitlisted_returns_resurfaced_and_updates_stage(self, conn, popen_calls):
        _insert_existing(conn, stage="waitlisted", score=7)
        result = _submit(conn, location="United States")
        assert result.status == "resurfaced"
        assert result.existing_stage == "waitlisted"
        row = conn.execute("SELECT stage, relevance_score FROM jobs").fetchone()
        assert row["stage"] == "scored"
        assert row["relevance_score"] == 8

    def test_scored_low_returns_resurfaced_and_bumps_score(self, conn, popen_calls):
        _insert_existing(conn, stage="scored", score=4)
        result = _submit(conn, location="United States")
        assert result.status == "resurfaced"
        row = conn.execute("SELECT relevance_score FROM jobs").fetchone()
        assert row["relevance_score"] == 8

    def test_manual_review_returns_resurfaced_and_promotes_stage(self, conn, popen_calls):
        _insert_existing(conn, stage="manual_review", score=6)
        result = _submit(conn, location="United States")
        assert result.status == "resurfaced"
        row = conn.execute("SELECT stage FROM jobs").fetchone()
        assert row["stage"] == "scored"

    def test_field_overwrite_on_resurface(self, conn, popen_calls):
        _insert_existing(conn, stage="rejected", score=4)
        result = _submit(conn, location="United States", raw_jd_text="Brand new JD content")
        assert result.status == "resurfaced"
        row = conn.execute("SELECT raw_jd_text FROM jobs").fetchone()
        assert row["raw_jd_text"] == "Brand new JD content"

    def test_fingerprint_populated_on_result(self, conn, popen_calls):
        _insert_existing(conn, stage="applied", score=8)
        result = _submit(conn, location="United States")
        assert result.fingerprint is not None

    def test_fingerprint_populated_on_fresh_insert(self, conn, popen_calls):
        result = _submit(conn)
        assert result.fingerprint is not None
