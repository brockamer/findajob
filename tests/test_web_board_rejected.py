"""Board Rejected tab — lists stage IN (rejected, not_selected), joins audit_log.

Schema note: audit_log.job_id stores jobs.id (UUID), not jobs.fingerprint. See
test_web_board_applied.py for the broader convention.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import ensure_view_prefs_table


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINDAJOB_MATERIALS_BASE_URL", "http://test:8090")
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, "
        "stage TEXT, reject_reason TEXT, url TEXT, created_at TEXT, stage_updated TEXT, "
        "prep_folder_path TEXT, relevance_score INTEGER, location TEXT, remote_status TEXT, "
        "ai_notes TEXT, user_notes TEXT, synthetic INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    one_day_ago = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    # User rejection: scored → rejected
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, reject_reason, url) "
        "VALUES ('id-rej','fp-rej','Wrong Stack','Acme','rejected','Tech Stack Mismatch','https://example.com/j1')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-rej','stage','scored','rejected',?,'user')",
        (three_days_ago,),
    )

    # Company rejection: applied → not_selected
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, reject_reason, url) "
        "VALUES ('id-ns','fp-ns','Principal Eng','Meta','not_selected','No Response','https://example.com/j2')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-ns','stage','applied','not_selected',?,'user')",
        (one_day_ago,),
    )

    # Active application — must NOT appear on /board/rejected
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage) "
        "VALUES ('id-app','fp-app','Staff Eng','Google','applied')"
    )

    ensure_view_prefs_table(conn)
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_rejected_lists_both_user_and_company_rejections(client: TestClient) -> None:
    r = client.get("/board/rejected")
    assert r.status_code == 200
    assert "Wrong Stack" in r.text
    assert "Principal Eng" in r.text
    # Rejection source flag
    assert ">user<" in r.text
    assert ">company<" in r.text
    # Reject reasons from jobs table
    assert "Tech Stack Mismatch" in r.text
    assert "No Response" in r.text


def test_rejected_excludes_active_applications(client: TestClient) -> None:
    r = client.get("/board/rejected")
    assert r.status_code == 200
    assert 'data-fingerprint="fp-app"' not in r.text
    assert "Staff Eng" not in r.text


def test_rejected_company_hyperlinks_to_materials(client: TestClient) -> None:
    r = client.get("/board/rejected")
    assert r.status_code == 200
    # Both rejected and not_selected are in FOLDER_STAGES, so company cells
    # hyperlink to /materials/{fingerprint}. The materials viewer then resolves
    # the folder on disk (_rejected/ for user, _applied/+marker for company).
    assert 'href="/materials/fp-rej"' in r.text
    assert 'href="/materials/fp-ns"' in r.text


def test_rejected_rows_filter_endpoint(client: TestClient) -> None:
    r = client.get("/board/rejected/rows?title=Principal")
    assert r.status_code == 200
    assert "Principal Eng" in r.text
    assert "Wrong Stack" not in r.text
