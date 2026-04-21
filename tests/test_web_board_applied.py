"""Board Applied tab — reads applied_date from audit_log, renders materials link."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINDAJOB_MATERIALS_BASE_URL", "http://test:8090")
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, user_notes TEXT, created_at TEXT, stage_updated TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    ten_days_ago = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage) "
        "VALUES ('fp-app','Eng Mgr','Anthropic','applied')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('fp-app','stage','materials_drafted','applied',?,'system')",
        (ten_days_ago,),
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db))


def test_applied_shows_row_with_age_class(client: TestClient) -> None:
    r = client.get("/board/applied")
    assert r.status_code == 200
    assert "Eng Mgr" in r.text
    assert "Anthropic" in r.text
    assert "row-applied-week" in r.text
    assert 'href="http://test:8090/materials/fp-app"' in r.text
