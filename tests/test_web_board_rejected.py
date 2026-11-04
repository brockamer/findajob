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
from tests.conftest import init_test_db


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINDAJOB_MATERIALS_BASE_URL", "http://test:8090")
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    three_days_ago = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    one_day_ago = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    # User rejection: scored → rejected
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, reject_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "id-rej",
            "fp-rej",
            "https://example.com/j1",
            "Wrong Stack",
            "Acme",
            "test",
            "rejected",
            "Tech Stack Mismatch",
        ),
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-rej','stage','scored','rejected',?,'user')",
        (three_days_ago,),
    )

    # Company rejection: applied → not_selected
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, reject_reason) "
        "VALUES ('id-ns','fp-ns','https://example.com/j2','Principal Eng','Meta','test','not_selected','No Response')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-ns','stage','applied','not_selected',?,'user')",
        (one_day_ago,),
    )

    # Active application — must NOT appear on /board/rejected
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
        "VALUES ('id-app','fp-app','https://example.com/j5','Staff Eng','Google','test','applied')"
    )

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
    r = client.get("/board/rejected/rows?title=Principal", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Principal Eng" in r.text
    assert "Wrong Stack" not in r.text


def _add_triage_dupe(client: TestClient) -> None:
    """A row the way triage writes a duplicate (orchestrator.py): stage='rejected'
    with dupe_of set, no score, no reject_reason, and a 'system' audit row."""
    db = client.app.state.db_path
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, dupe_of) "
        "VALUES ('id-dupe','fp-dupe','https://example.com/j9','Syndicated Copy Role','Acme',"
        "'gmail_linkedin','rejected','id-rej')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-dupe','stage','discovered','rejected',datetime('now'),'system')"
    )
    conn.commit()
    conn.close()


def test_rejected_excludes_triage_duplicates(client: TestClient) -> None:
    """A triage duplicate is not a user rejection: it must not appear on the
    Rejected tab (labelled 'user', with an un-reject button) next to real passes."""
    _add_triage_dupe(client)
    r = client.get("/board/rejected")
    assert r.status_code == 200
    assert "Wrong Stack" in r.text
    assert "Principal Eng" in r.text
    assert "Syndicated Copy Role" not in r.text


def test_materials_rejected_section_excludes_triage_duplicates(client: TestClient) -> None:
    """The /materials/ Rejected section lists newest-first under a cap, so
    duplicates from one large triage run would push every real rejection out."""
    _add_triage_dupe(client)
    r = client.get("/materials/")
    assert r.status_code == 200
    assert "Wrong Stack" in r.text
    assert "Syndicated Copy Role" not in r.text
