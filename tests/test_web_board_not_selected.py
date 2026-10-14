"""Board Not Selected tab — lists stage='not_selected' only, joins audit_log
for the not_selected_date column.

Sibling to test_web_board_rejected.py; coverage gap surfaced by #698 advisor
check before PR.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINDAJOB_MATERIALS_BASE_URL", "http://test:8090")
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    one_day_ago = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    # Two not_selected rows
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, reject_reason, location) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "id-ns1",
            "fp-ns1",
            "https://example.com/j1",
            "Principal Eng",
            "Meta",
            "test",
            "not_selected",
            "No Response",
            "Menlo Park",
        ),
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-ns1','stage','applied','not_selected',?,'user')",
        (one_day_ago,),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, reject_reason) "
        "VALUES ('id-ns2','fp-ns2','https://example.com/j2','Director','Google','test','not_selected','Compensation')"
    )

    # User rejection — must NOT appear on /board/not-selected (rejected, not not_selected)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, reject_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "id-rej",
            "fp-rej",
            "https://example.com/j3",
            "Wrong Stack",
            "Acme",
            "test",
            "rejected",
            "Tech Stack Mismatch",
        ),
    )

    # Active application — must NOT appear either
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
        "VALUES ('id-app','fp-app','https://example.com/j4','Staff Eng','Stripe','test','applied')"
    )

    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_not_selected_lists_only_not_selected_rows(client: TestClient) -> None:
    r = client.get("/board/not-selected")
    assert r.status_code == 200
    # Both not_selected rows surface
    assert "Principal Eng" in r.text
    assert "Director" in r.text
    # The rejected and applied rows do NOT
    assert "Wrong Stack" not in r.text
    assert "Staff Eng" not in r.text


def test_not_selected_excludes_active_applications(client: TestClient) -> None:
    r = client.get("/board/not-selected")
    assert r.status_code == 200
    assert 'data-fingerprint="fp-app"' not in r.text
    assert 'data-fingerprint="fp-rej"' not in r.text


def test_not_selected_renders_audit_log_date(client: TestClient) -> None:
    """The audit_log JOIN populates `not_selected_date` for the row with an
    audit row; rows without one render an empty date cell rather than crashing."""
    r = client.get("/board/not-selected")
    assert r.status_code == 200
    # fp-ns1 has an audit_log row from "one_day_ago"; the year prefix should
    # appear in the rendered date column.
    current_year = str(datetime.now(UTC).year)
    assert current_year in r.text  # date cell renders
    # fp-ns2 has no audit_log row but the row should still render (LEFT JOIN)
    assert 'data-fingerprint="fp-ns2"' in r.text


def test_not_selected_rows_filter_endpoint(client: TestClient) -> None:
    r = client.get("/board/not-selected/rows?title=Principal", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Principal Eng" in r.text
    assert "Director" not in r.text
