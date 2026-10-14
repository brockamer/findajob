"""HTMX filter endpoint: /board/<tab>/rows?q=<text> narrows rows by title + company."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    for i, (fp, title, company) in enumerate(
        [
            ("fp1", "NPI PM", "Meta"),
            ("fp2", "Staff Eng", "Anthropic"),
            ("fp3", "TPM", "Meta"),
        ]
    ):
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
            "VALUES (?, ?, ?, ?, ?, 'test', 'scored', 8)",
            (f"jid-{i}", fp, f"https://x.test/{fp}", title, company),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_dashboard_filter_narrows_by_company(client: TestClient) -> None:
    r = client.get("/board/dashboard/rows?company=meta", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "NPI PM" in r.text
    assert "TPM" in r.text
    assert "Staff Eng" not in r.text


def test_filter_fragment_has_no_body_tag(client: TestClient) -> None:
    r = client.get("/board/dashboard/rows?q=", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<body" not in r.text.lower()
