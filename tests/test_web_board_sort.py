"""Sort via ?sort=<col>&desc=<0|1> works for each board tab."""

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
        "fit_score REAL, probability_score REAL, relevance_score INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, user_notes TEXT, score_flag_reason TEXT, source TEXT, url TEXT, "
        "created_at TEXT, stage_updated TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    # Three scored jobs spanning score values, passing the Dashboard WHERE
    for fp, company, score in [("fp-a", "Alpha", 9.0), ("fp-b", "Bravo", 7.5), ("fp-c", "Charlie", 8.2)]:
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, stage, fit_score, created_at) "
            "VALUES (?, 't', ?, 'scored', ?, '2026-04-01')",
            (fp, company, score),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db))


def test_dashboard_sort_by_fit_score_desc(client: TestClient) -> None:
    r = client.get("/board/dashboard?sort=fit_score&desc=1")
    assert r.status_code == 200
    # Highest fit_score row (Alpha, 9.0) should appear before Charlie (8.2) before Bravo (7.5)
    i_alpha = r.text.find("Alpha")
    i_charlie = r.text.find("Charlie")
    i_bravo = r.text.find("Bravo")
    assert 0 < i_alpha < i_charlie < i_bravo, (
        f"Expected Alpha < Charlie < Bravo in desc-by-fit_score order, got positions {i_alpha}, {i_charlie}, {i_bravo}"
    )


def test_dashboard_sort_by_fit_score_asc(client: TestClient) -> None:
    r = client.get("/board/dashboard?sort=fit_score&desc=0")
    assert r.status_code == 200
    i_alpha = r.text.find("Alpha")
    i_charlie = r.text.find("Charlie")
    i_bravo = r.text.find("Bravo")
    assert 0 < i_bravo < i_charlie < i_alpha


def test_dashboard_unknown_sort_falls_back_to_default(client: TestClient) -> None:
    # default sort is fit_score DESC (per _DASHBOARD_DEFAULT_SORT)
    r = client.get("/board/dashboard?sort=not_a_real_column&desc=1")
    assert r.status_code == 200
    # Should not crash; rows render
    assert "Alpha" in r.text
