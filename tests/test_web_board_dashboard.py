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
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, created_at TEXT, stage_updated TEXT, url TEXT, prep_folder_path TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, url) "
        "VALUES ('fp1','Senior DC Ops','Meta','scored',8,'https://example.com/meta-dc-ops')"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, url) "
        "VALUES ('fp2','NPI PM','Google','materials_drafted',9,'https://example.com/google-npi')"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, url) "
        "VALUES ('fp3','Junior','Acme','scored',3,'https://example.com/acme-jr')"
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


def test_dashboard_defaults_to_compact_density(client: TestClient) -> None:
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "density-compact" in r.text
    # Active density button carries the filled style
    assert "bg-slate-900 text-white" in r.text


def test_dashboard_expanded_density_param(client: TestClient) -> None:
    r = client.get("/board/dashboard?density=expanded")
    assert r.status_code == 200
    assert "density-expanded" in r.text
    assert "density-compact" not in r.text


def test_dashboard_rejects_invalid_density(client: TestClient) -> None:
    """Unknown density value falls back to the default (compact)."""
    r = client.get("/board/dashboard?density=nonsense")
    assert r.status_code == 200
    assert "density-compact" in r.text
    assert "density-nonsense" not in r.text


def test_dashboard_rows_have_cell_text_wrapper_with_title(client: TestClient) -> None:
    """Each text cell wraps its content in .cell-text-wrap with a title tooltip."""
    r = client.get("/board/dashboard")
    assert "cell-text-wrap" in r.text
    # At least one cell has a title attribute populated from the row data
    assert 'title="Senior DC Ops"' in r.text


def test_dashboard_title_links_to_job_url(client: TestClient) -> None:
    """Title cell on each row hyperlinks to the original job URL, opens in new tab."""
    r = client.get("/board/dashboard")
    assert 'href="https://example.com/meta-dc-ops"' in r.text
    assert 'target="_blank"' in r.text
    assert 'rel="noopener noreferrer"' in r.text
