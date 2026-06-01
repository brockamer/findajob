"""Stats config-change popover route — /stats/config-change/{date} (#953).

Regression guard: the route built its cost filter against a nonexistent
`cost_log.timestamp` column, so every config-change marker popover 500'd in
production while a fabricated-schema unit fixture kept the suite green. This
test exercises the route against the REAL schema (via init_test_db) so the
500 can't come back masked.
"""

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
    # A real cost_log row in the "after" window — exercises the SUM(cost_usd)
    # path that raised OperationalError on the bad column.
    conn.execute(
        "INSERT INTO cost_log (logged_at, cost_usd, operation, model) VALUES (?, ?, ?, ?)",
        ("2026-05-17 10:00:00", 2.50, "score", "scorer"),
    )
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_config_change_detail_returns_200(client: TestClient) -> None:
    resp = client.get("/stats/config-change/2026-05-15")
    assert resp.status_code == 200
    body = resp.json()
    assert "before" in body
    assert "after" in body
    # The after window contains the seeded $2.50 cost row.
    assert body["after"]["total_cost"] == 2.50


def test_config_change_detail_invalid_date_returns_400(client: TestClient) -> None:
    resp = client.get("/stats/config-change/not-a-date")
    assert resp.status_code == 400
