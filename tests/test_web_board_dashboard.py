"""Board Dashboard tab."""

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
        "relevance_score INTEGER, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, created_at TEXT, stage_updated TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score) "
        "VALUES ('fp1','Senior DC Ops','Meta','scored',8)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score) "
        "VALUES ('fp2','NPI PM','Google','materials_drafted',9)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score) "
        "VALUES ('fp3','Junior','Acme','scored',3)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db))


def test_dashboard_shows_in_scope_jobs(client: TestClient) -> None:
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "Senior DC Ops" in r.text
    assert "NPI PM" in r.text
    # fp3 (score=3) is filtered out by score<7. Check by fingerprint — the
    # reject-reason dropdown now contains substrings like "Too Junior" for
    # every rendered row, so bare-word "Junior" is a false-positive signal.
    assert 'data-fingerprint="fp3"' not in r.text
