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
    dupe_of TEXT DEFAULT ''
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

    second = _submit(conn)  # identical submission
    assert second.status == "duplicate"
    assert second.existing_match == "strict"
    assert second.job_id == first.job_id

    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert count == 1


def test_url_duplicate(conn: sqlite3.Connection, popen_calls):
    first = _submit(conn)
    # Same URL, different title/location → strict fingerprint misses, URL matches
    second = _submit(
        conn,
        title="Operations Manager",
        location="Remote",
    )
    assert second.status == "duplicate"
    assert second.existing_match == "url"
    assert second.job_id == first.job_id


def test_tier2_loose_dedup_when_existing_has_coarse_location(conn: sqlite3.Connection, popen_calls):
    # Existing row has coarse location ("" / "US"); new row comes in with a
    # specific city for the same (company, title). Tier 2 should match —
    # this is the cross-source syndication case from #182 Bug C.
    first = _submit(conn, location="United States", url="https://greenhouse.io/a/1")
    second = _submit(conn, location="Barstow, TX", url="https://linkedin.com/jobs/999")
    assert second.status == "duplicate"
    assert second.existing_match == "loose"
    assert second.job_id == first.job_id


def test_tier2_loose_dedup_when_incoming_has_coarse_location(conn: sqlite3.Connection, popen_calls):
    _submit(conn, location="Barstow, TX", url="https://greenhouse.io/a/1")
    second = _submit(conn, location="", url="https://linkedin.com/jobs/999")
    assert second.status == "duplicate"
    assert second.existing_match == "loose"


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
