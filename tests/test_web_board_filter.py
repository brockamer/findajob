"""HTMX filter endpoint: /board/<tab>/rows?q=<text> narrows rows by title + company."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, url TEXT, created_at TEXT, stage_updated TEXT, prep_folder_path TEXT)"
    )
    for fp, title, company in [
        ("fp1", "NPI PM", "Meta"),
        ("fp2", "Staff Eng", "Anthropic"),
        ("fp3", "TPM", "Meta"),
    ]:
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score) VALUES (?, ?, ?, 'scored', 8)",
            (fp, title, company),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_dashboard_filter_narrows_by_company(client: TestClient) -> None:
    r = client.get("/board/dashboard/rows?q=meta")
    assert r.status_code == 200
    assert "NPI PM" in r.text
    assert "TPM" in r.text
    assert "Staff Eng" not in r.text


def test_filter_fragment_has_no_body_tag(client: TestClient) -> None:
    r = client.get("/board/dashboard/rows?q=")
    assert r.status_code == 200
    assert "<body" not in r.text.lower()


def test_filter_preserves_sort_via_hidden_inputs(client: TestClient) -> None:
    """The filter form carries hidden sort/desc inputs so hx-include can
    forward them to the /rows endpoint — filtering should not strip sort."""
    r = client.get("/board/dashboard?sort=title&desc=0")
    assert r.status_code == 200
    # Hidden inputs exist with the server-echoed values
    assert 'name="sort" value="title"' in r.text
    assert 'name="desc" value="0"' in r.text
