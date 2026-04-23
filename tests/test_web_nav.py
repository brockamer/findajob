"""_nav.html partial highlights the current route."""

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
        "fit_score REAL, probability_score REAL, relevance_score INTEGER, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, user_notes TEXT, score_flag_reason TEXT, source TEXT, reject_reason TEXT, url TEXT, "
        "created_at TEXT, stage_updated TEXT, prep_folder_path TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    app = create_app(companies_root=companies, db_path=db)
    return TestClient(app)


def test_nav_present_on_landing(client: TestClient) -> None:
    r = client.get("/materials/")
    assert r.status_code == 200
    assert 'href="/"' in r.text
    assert 'href="/materials/"' in r.text
    assert 'href="/board/dashboard"' in r.text
    assert 'href="/ingest/"' in r.text
    assert 'href="/stats/funnel"' in r.text
    assert 'href="/tools/"' in r.text
    assert 'href="/config/"' in r.text
    assert 'href="/docs/"' in r.text


def test_materials_index_moved(client: TestClient) -> None:
    r = client.get("/materials/")
    assert r.status_code == 200
    assert "In flight" in r.text or "Applied" in r.text or "Rejected" in r.text


def test_every_nav_link_resolves(client: TestClient) -> None:
    """Regression: every href in the top nav returns 200, not 404.

    /stats/funnel uses follow_redirects=True to absorb the /stats/ → /stats/funnel
    redirect (the link points at /stats/funnel directly, so this is just defensive).
    """
    for path in ["/", "/materials/", "/board/dashboard", "/ingest/", "/stats/funnel", "/tools/", "/config/", "/docs/"]:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200, f"Nav link {path} returned {r.status_code}"


def test_board_link_highlights_on_every_board_page(client: TestClient) -> None:
    """Regression for #138: Board link in top nav highlights on /board/applied,
    /board/waitlist, etc., not just on /board/dashboard."""
    for path in ["/board/dashboard", "/board/applied", "/board/waitlist", "/board/review", "/board/archive"]:
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        idx = r.text.index('href="/board/dashboard"')
        snippet = r.text[idx : idx + 300]
        assert 'aria-current="page"' in snippet, f"Board link not active on {path}"
