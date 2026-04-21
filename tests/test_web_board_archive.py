"""Archive tab: pagination, HTMX sentinel."""

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
        "fit_score REAL, location TEXT, remote_status TEXT, source TEXT, url TEXT, "
        "created_at TEXT, stage_updated TEXT)"
    )
    for i in range(150):
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, stage, fit_score, created_at) "
            "VALUES (?, ?, ?, 'scored', ?, '2026-01-01')",
            (f"fp-{i:03}", f"Role {i}", f"Co {i}", 1.0 + i % 10),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db))


def test_archive_first_page_100_rows_plus_sentinel(client: TestClient) -> None:
    r = client.get("/board/archive")
    assert r.status_code == 200
    # 100 data rows + header row + sentinel row
    assert r.text.count("<tr") >= 101
    assert "offset=100" in r.text


def test_archive_second_page_via_rows_endpoint(client: TestClient) -> None:
    r = client.get("/board/archive/rows?offset=100")
    assert r.status_code == 200
    # Remaining 50 rows, no sentinel (end reached)
    assert r.text.count("<tr") == 50
    assert "offset=" not in r.text


def test_archive_rows_endpoint_returns_fragment_not_full_page(client: TestClient) -> None:
    r = client.get("/board/archive/rows?offset=0")
    assert r.status_code == 200
    assert "<body" not in r.text.lower()
