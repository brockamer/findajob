"""recall-audit page labels its grouping correctly (#967 item 2).

The route GROUP BYs date(audited_at) — one row per audit *date*, not per ISO
week — but the template called every row a "Week", which lies the moment the
audit runs twice in one week. This pins the date-accurate labeling.
"""

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
        "CREATE TABLE recall_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, "
        "audited_at TEXT, original_score INTEGER, original_scored_by TEXT, auditor_model TEXT NOT NULL, "
        "audited_score INTEGER, upgraded INTEGER DEFAULT 0, audit_notes TEXT)"
    )
    # 25 samples on one audit date — N >= 20 so the row is not min-N gated.
    for i in range(25):
        conn.execute(
            "INSERT INTO recall_audit (job_id, audited_at, auditor_model, upgraded) VALUES (?, ?, ?, ?)",
            (f"j{i}", "2026-05-31 09:00:00", "test-model", 1 if i < 3 else 0),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_recall_audit_labels_grouping_by_date_not_week(client: TestClient) -> None:
    r = client.get("/stats/recall-audit")
    assert r.status_code == 200
    assert "2026-05-31" in r.text  # the audit date renders
    assert ">Audit date<" in r.text  # column header reflects per-date grouping
    assert ">Week<" not in r.text  # the misleading "Week" column header is gone
