"""Landing page shows stage counts."""

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
    for stage, n in [("scored", 5), ("applied", 2), ("rejected", 3)]:
        for i in range(n):
            conn.execute(
                "INSERT INTO jobs (fingerprint, title, company, stage, created_at) "
                "VALUES (?, 't', 'c', ?, '2026-01-01')",
                (f"fp-{stage}-{i}", stage),
            )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    app = create_app(companies_root=companies, db_path=db)
    return TestClient(app)


def test_landing_shows_stage_counts(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert ">5<" in r.text and "scored" in r.text
    assert ">2<" in r.text and "applied" in r.text
    assert ">3<" in r.text and "rejected" in r.text


def test_landing_nav_home_active(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert 'aria-current="page"' in r.text


@pytest.mark.parametrize(
    "path,label,issue",
    [
        # /ingest/ promoted from placeholder to a real route in #62 — covered by tests/test_web_ingest.py.
        ("/tools/", "Tools", ""),
        ("/config/", "Config", ""),
        ("/docs/", "Docs", ""),
    ],
)
def test_placeholder_renders(client: TestClient, path: str, label: str, issue: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert "Coming soon" in r.text
    assert label in r.text
