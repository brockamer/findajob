"""Board Review tab."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import ensure_view_prefs_table


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "score_flag_reason TEXT, source TEXT, user_notes TEXT, url TEXT, created_at TEXT, stage_updated TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, score_flag_reason, source, created_at) "
        "VALUES ('fp-rev','Ambiguous Title','Meta','manual_review','target company bump','greenhouse','2026-04-20')"
    )
    conn.execute("INSERT INTO jobs (fingerprint, title, company, stage) VALUES ('fp-scored','Other','Acme','scored')")
    ensure_view_prefs_table(conn)
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_review_shows_manual_review_only(client: TestClient) -> None:
    r = client.get("/board/review")
    assert r.status_code == 200
    assert "Ambiguous Title" in r.text
    # fp-scored isn't in manual_review, so its row should not render.
    # Match the fingerprint attribute — "Other" as a bare word now appears in
    # the reject-reason dropdown for every rendered row.
    assert 'data-fingerprint="fp-scored"' not in r.text
    assert "target company bump" in r.text
