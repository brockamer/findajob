"""Integration tests for the manual-ingest web route (#62).

Exercises ``GET /ingest/`` and ``POST /ingest/manual`` against a real
TestClient-backed FastAPI app + on-disk SQLite. ``subprocess.Popen`` is
monkeypatched on the ``findajob.ingest`` module so no prep_application.py
fork happens during tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import utils
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
    dupe_of TEXT DEFAULT ''
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
def popen_calls(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args, **_kw):
            calls.append(args)

    from findajob import ingest as ingest_mod

    monkeypatch.setattr(ingest_mod.subprocess, "Popen", _FakePopen)
    return calls


@pytest.fixture()
def client(tmp_path: Path, monkeypatch, popen_calls) -> TestClient:
    monkeypatch.setattr(utils, "LOG_PATH", str(tmp_path / "events.jsonl"))

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
    # Speculative mode stub must be present so #131 slots in cleanly.
    assert "#131" in html


def test_post_success_inserts_row(client: TestClient, popen_calls) -> None:
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
    assert popen_calls == []  # no generate_folder checkbox


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


def test_post_duplicate_returns_duplicate_partial(client: TestClient) -> None:
    first = client.post("/ingest/manual", data=_VALID_FORM)
    assert 'data-outcome="success"' in first.text

    second = client.post("/ingest/manual", data=_VALID_FORM)
    assert second.status_code == 200
    assert 'data-outcome="duplicate"' in second.text
    assert "strict" in second.text  # reports the match tier
    assert _job_count(client) == 1


def test_generate_folder_launches_prep(client: TestClient, popen_calls) -> None:
    data = dict(_VALID_FORM)
    data["generate_folder"] = "true"
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    assert "Prep folder generation started" in resp.text
    assert len(popen_calls) == 1
    assert popen_calls[0][1].endswith("/scripts/prep_application.py")


def test_generate_folder_deferred_when_prep_queue_full(client: TestClient, popen_calls) -> None:
    # Fill the prep-in-flight cap with 3 jobs stuck in prep_in_progress.
    conn = sqlite3.connect(client._db_path)  # type: ignore[attr-defined]
    for i in range(3):
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage)"
            " VALUES (?, ?, ?, ?, ?, 'test', 'prep_in_progress')",
            (f"id_{i}", f"fp_{i}", f"https://x/{i}", f"T{i}", f"C{i}"),
        )
    conn.commit()
    conn.close()

    data = dict(_VALID_FORM)
    data["generate_folder"] = "true"
    resp = client.post("/ingest/manual", data=data)
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    # Row inserted, but prep NOT launched — cap enforced.
    assert "Prep queue is full" in resp.text
    assert popen_calls == []
    # Row created (4 total now: 3 seed + 1 new).
    assert _job_count(client) == 4
