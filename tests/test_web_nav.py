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
        "CREATE TABLE jobs (fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "fit_score REAL, created_at TEXT, stage_updated TEXT)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    app = create_app(companies_root=companies, db_path=db)
    return TestClient(app)


def test_nav_present_on_landing(client: TestClient) -> None:
    # / has no handler yet (landing arrives in Task 6); test nav on /materials/ in the meantime.
    r = client.get("/materials/")
    assert r.status_code == 200
    assert 'href="/"' in r.text
    assert 'href="/materials/"' in r.text
    assert 'href="/board/"' in r.text
    assert 'href="/ingest/"' in r.text
    assert 'href="/tools/"' in r.text
    assert 'href="/config/"' in r.text
    assert 'href="/docs/"' in r.text


def test_materials_index_moved(client: TestClient) -> None:
    r = client.get("/materials/")
    assert r.status_code == 200
    assert "In flight" in r.text or "Applied" in r.text or "Rejected" in r.text


def test_every_nav_link_resolves(client: TestClient) -> None:
    """Regression: every href in the top nav returns 200, not 404."""
    for path in ["/", "/materials/", "/board/", "/ingest/", "/tools/", "/config/", "/docs/"]:
        r = client.get(path)
        assert r.status_code == 200, f"Nav link {path} returned {r.status_code}"
