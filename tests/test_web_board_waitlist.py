"""Board Waitlist tab — shows waitlisted jobs and computes blocking_app subquery."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, location TEXT, remote_status TEXT, ai_notes TEXT, "
        "created_at TEXT, stage_updated TEXT)"
    )
    # Meta has two jobs: one waitlisted, one actively applied — blocking_app should surface
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, stage_updated) "
        "VALUES ('fp-wait','Ops Lead','Meta','waitlisted',8,'2026-04-18')"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, stage_updated) "
        "VALUES ('fp-blocker','NPI PM','Meta','applied','2026-04-20')"
    )
    # Anthropic has only a waitlisted job; blocking_app should be NULL
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score) "
        "VALUES ('fp-solo','Eng','Anthropic','waitlisted',6)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db))


def test_waitlist_shows_waitlisted_and_blocking_app(client: TestClient) -> None:
    r = client.get("/board/waitlist")
    assert r.status_code == 200
    assert "Ops Lead" in r.text
    assert "Eng" in r.text
    # Meta's blocking app appears in the same row
    assert "NPI PM" in r.text
    assert "applied" in r.text
