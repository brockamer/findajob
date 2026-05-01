"""Board Applied tab — reads applied_date from audit_log, renders materials link.

Production schema note: audit_log.job_id stores jobs.id (UUID), not
jobs.fingerprint. Also, some jobs skip 'applied' and go straight to
'interview' (recruiter flows), so the applied_date lookup joins on any
post-application stage transition. Both behaviors are asserted below
to prevent regression against production.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINDAJOB_MATERIALS_BASE_URL", "http://test:8090")
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, "
        "stage TEXT, location TEXT, remote_status TEXT, known_contacts TEXT, "
        "comp_estimate TEXT, ai_notes TEXT, user_notes TEXT, url TEXT, "
        "created_at TEXT, stage_updated TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    ten_days_ago = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    five_days_ago = (datetime.now(UTC) - timedelta(days=5)).isoformat()

    # Normal flow: user applied 10 days ago → row-applied-week bucket
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage) "
        "VALUES ('id-app','fp-app','Eng Mgr','Anthropic','applied')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-app','stage','materials_drafted','applied',?,'system')",
        (ten_days_ago,),
    )

    # Recruiter flow: skipped 'applied', went straight to 'interview' 5 days ago.
    # applied_date should resolve via the new_value IN (...) clause.
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage) "
        "VALUES ('id-int','fp-int','Principal Eng','Meta','interview')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('id-int','stage','materials_drafted','interview',?,'system')",
        (five_days_ago,),
    )

    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_applied_shows_row_with_age_class(client: TestClient) -> None:
    r = client.get("/board/applied")
    assert r.status_code == 200
    assert "Eng Mgr" in r.text
    assert "Anthropic" in r.text
    # 10-day-old applied row → row-applied-week (yellow bucket)
    assert "row-applied-week" in r.text
    # materials hyperlink is same-origin relative (works behind any reverse proxy)
    assert 'href="/materials/fp-app"' in r.text


def test_applied_recruiter_flow_captures_interview_as_applied_date(client: TestClient) -> None:
    """A job that skipped 'applied' and went straight to 'interview' still
    gets an applied_date so row-aging works (mirrors sync_sheet.py line 585)."""
    r = client.get("/board/applied")
    assert r.status_code == 200
    # 5-day-old interview row → row-applied-fresh (green bucket); without the
    # broader new_value IN (...) clause, applied_date would be NULL and no
    # bucket class would render on this row.
    assert "Principal Eng" in r.text
    assert "row-applied-fresh" in r.text


def test_applied_status_cell_writes_pending_action_to_reject_select(client: TestClient) -> None:
    """#361 regression — pending-action lives on the reject select's own dataset,
    not the parent <tr>. Mobile Chrome was losing the row-level dataset between
    cell focus events and routing the reject pick to /reject instead of
    /not-selected. Asserts the JS contract end-to-end on the rendered HTML."""
    r = client.get("/board/applied")
    assert r.status_code == 200
    # Status cell must set the pending action on the reject select directly.
    assert "rejectSel.dataset.pendingAction='not-selected'" in r.text
    # Reject cell must read from its own dataset (this.dataset), not tr.dataset.
    assert "this.dataset.pendingAction==='not-selected'" in r.text
    # Defensive: the old tr.dataset.pendingAction pattern must NOT reappear.
    assert "tr.dataset.pendingAction" not in r.text


def test_base_template_surfaces_htmx_response_errors(client: TestClient) -> None:
    """#361 — silent htmx failures (4xx/5xx no-swap) used to leave the row
    unchanged with no user-visible feedback. The base template wires a global
    listener (in /static/htmx_errors.js) that renders the toast container."""
    r = client.get("/board/applied")
    assert r.status_code == 200
    assert "htmx-error-toast" in r.text
    assert "/static/htmx_errors.js" in r.text
