"""Integration tests for the manual-ingest web route (#62).

Exercises ``GET /ingest/`` and ``POST /ingest/manual`` against a real
TestClient-backed FastAPI app + on-disk SQLite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.web.app import create_app

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

CREATE TABLE speculative_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    hint TEXT,
    personal_notes TEXT,
    status TEXT NOT NULL DEFAULT 'researching',
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    research_completed_at TEXT,
    approved_at TEXT
);
"""

_VALID_FORM: dict[str, str] = {
    "company": "Acme Data Centers",
    "title": "Senior Operations Engineer",
    "url": "https://boards.greenhouse.io/acme/jobs/42",
    "raw_jd_text": "Lead a team of ops engineers…",
    "location": "Menlo Park, CA",
    "remote_status": "On-site",
    "notes": "",
    "known_contacts": "",
}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    app = create_app(companies_root=companies, db_path=db_path)
    client = TestClient(app)
    client._db_path = db_path  # type: ignore[attr-defined]
    return client


def _job_count(client: TestClient) -> int:
    conn = sqlite3.connect(client._db_path)  # type: ignore[attr-defined]
    n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    return int(n)


def _fetch_one(client: TestClient) -> sqlite3.Row:
    conn = sqlite3.connect(client._db_path)  # type: ignore[attr-defined]
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs").fetchone()
    conn.close()
    assert row is not None
    return row


def test_get_renders_form(client: TestClient) -> None:
    resp = client.get("/ingest/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="manual-ingest-form"' in html
    assert 'hx-post="/ingest/manual"' in html
    # Speculative mode (#131) is now wired — toggle and form should be present.
    assert 'action="/ingest/speculative"' in html
    assert "Submit speculative" in html


def test_post_success_inserts_row(client: TestClient) -> None:
    resp = client.post("/ingest/manual", data=_VALID_FORM)
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    assert "Ingested" in resp.text

    assert _job_count(client) == 1
    row = _fetch_one(client)
    assert row["source"] == "web_manual"
    assert row["stage"] == "scored"
    assert row["relevance_score"] == 8
    assert row["raw_jd_text"] == "Lead a team of ops engineers…"


def test_post_missing_required_returns_error_partial(client: TestClient) -> None:
    data = dict(_VALID_FORM)
    data["raw_jd_text"] = "   "  # whitespace-only fails the non-empty check
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="error"' in resp.text
    assert "Missing required field" in resp.text
    assert _job_count(client) == 0


def test_post_missing_required_formkey_returns_422(client: TestClient) -> None:
    # FastAPI's Form(...) rejects absent fields before our handler runs.
    data = {k: v for k, v in _VALID_FORM.items() if k != "company"}
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 422


def test_post_duplicate_returns_resurfaced_partial(client: TestClient) -> None:
    """A second identical submission strict-matches the first, which is at
    stage=scored — so the result is resurfaced, not duplicate."""
    first = client.post("/ingest/manual", data=_VALID_FORM)
    assert 'data-outcome="success"' in first.text

    second = client.post("/ingest/manual", data=_VALID_FORM)
    assert second.status_code == 200
    assert 'data-outcome="resurfaced"' in second.text
    assert _job_count(client) == 1


def _insert_existing_job(db_path: str, *, stage: str, score: int = 8, folder: str | None = None) -> None:
    """Seed a pre-existing job in the given stage directly into the DB."""
    from findajob.cleaning import clean_company, clean_title, fingerprint, loose_fingerprint

    co = clean_company("Acme Data Centers")
    ti = clean_title("Senior Operations Engineer")
    # location must be coarse so the loose-dedup tier fires
    loc = "United States"
    fp = fingerprint(ti, co, loc)
    lfp = loose_fingerprint(ti, co)
    job_id = f"triage-{fp}"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO jobs
           (id, fingerprint, loose_fingerprint, url, title, company, location,
            source, relevance_score, stage, apply_flag, prep_folder_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'triage', ?, ?, 0, ?)""",
        (job_id, fp, lfp, "https://example.com/original", ti, co, loc, score, stage, folder),
    )
    conn.commit()
    conn.close()


def test_duplicate_applied_returns_already_applied_partial(client: TestClient) -> None:
    # The existing row has a coarse location so the loose dedup tier fires.
    _insert_existing_job(client._db_path, stage="applied")  # type: ignore[attr-defined]
    # Submit with a specific city — will match via loose tier.
    data = dict(_VALID_FORM)
    data["location"] = "San Francisco, CA"
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="already_applied"' in resp.text
    assert "Already applied" in resp.text
    assert "/board/applied" in resp.text
    # DB row count unchanged (1 pre-existing row, no new insert)
    assert _job_count(client) == 1


def test_duplicate_not_selected_returns_not_selected_partial(client: TestClient) -> None:
    _insert_existing_job(client._db_path, stage="not_selected", folder="/tmp/fake_folder")  # type: ignore[attr-defined]
    data = dict(_VALID_FORM)
    data["location"] = "San Francisco, CA"
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="not_selected"' in resp.text
    assert "not selected" in resp.text.lower()
    assert "/board/rejected" in resp.text
    assert _job_count(client) == 1


def test_duplicate_rejected_returns_resurfaced_and_updates_db(client: TestClient) -> None:
    _insert_existing_job(client._db_path, stage="rejected", score=4)  # type: ignore[attr-defined]
    data = dict(_VALID_FORM)
    data["location"] = "San Francisco, CA"
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="resurfaced"' in resp.text
    assert "/board/dashboard" in resp.text
    # Stage must be updated in DB
    conn = sqlite3.connect(client._db_path)  # type: ignore[attr-defined]
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT stage, relevance_score FROM jobs").fetchone()
    conn.close()
    assert row["stage"] == "scored"
    assert row["relevance_score"] == 8


def test_duplicate_waitlisted_returns_resurfaced(client: TestClient) -> None:
    _insert_existing_job(client._db_path, stage="waitlisted", score=7)  # type: ignore[attr-defined]
    data = dict(_VALID_FORM)
    data["location"] = "San Francisco, CA"
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="resurfaced"' in resp.text
    conn = sqlite3.connect(client._db_path)  # type: ignore[attr-defined]
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT stage FROM jobs").fetchone()
    conn.close()
    assert row["stage"] == "scored"


def test_duplicate_low_scored_returns_resurfaced_and_bumps_score(client: TestClient) -> None:
    _insert_existing_job(client._db_path, stage="scored", score=3)  # type: ignore[attr-defined]
    data = dict(_VALID_FORM)
    data["location"] = "San Francisco, CA"
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="resurfaced"' in resp.text
    conn = sqlite3.connect(client._db_path)  # type: ignore[attr-defined]
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT relevance_score FROM jobs").fetchone()
    conn.close()
    assert row["relevance_score"] == 8
