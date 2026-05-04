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
    gets an applied_date so row-aging works."""
    r = client.get("/board/applied")
    assert r.status_code == 200
    # 5-day-old interview row → row-applied-fresh (green bucket); without the
    # broader new_value IN (...) clause, applied_date would be NULL and no
    # bucket class would render on this row.
    assert "Principal Eng" in r.text
    assert "row-applied-fresh" in r.text


def test_applied_status_cell_fires_not_selected_one_click(client: TestClient) -> None:
    """#391 — picking 'Not Selected' on the Applied tab fires
    /not-selected immediately, no two-step reason picker. The earlier
    coordination scheme (#361 / #374) made the user pick a status THEN
    a reason, which the operator surfaced as confusing UX in #391:
    'I click Not Selected and I just get this message Pick a reason.
    The reason IS not selected.'

    The fix removes the dataset.pendingAction handoff, the
    js-status-hint span, the reject-cell focus-and-pulse, and the
    'Pick a reason →' hint. The status cell now POSTs the chosen
    action straight to its endpoint regardless of which option fires."""
    r = client.get("/board/applied")
    assert r.status_code == 200

    # The one-click POST shape must be present — picking a status fires
    # htmx.ajax with /board/jobs/{fp}/${this.value} immediately.
    assert "htmx.ajax('POST',`/board/jobs/" in r.text
    assert "${this.value}`" in r.text

    # The two-step coordination MUST be gone in its entirety.
    assert "rejectSel.dataset.pendingAction" not in r.text, (
        "#391: dataset.pendingAction handoff was the symptom of the old two-step UX. It should not reappear."
    )
    assert "tr.dataset.pendingAction" not in r.text
    assert "this.dataset.pendingAction" not in r.text
    assert "Pick a reason" not in r.text
    assert "js-status-hint" not in r.text


def test_applied_reject_cell_renders_inert_dash(client: TestClient) -> None:
    """#391 — every transition off Applied is now one-click via the
    status cell. The reject cell's role on Applied (collect a reason
    for /not-selected) is gone with the two-step. The cell renders an
    inert dash so the column position aligns visually with other tabs
    where the cell is still active (Dashboard / Review / Waitlist).

    The reject cell must NOT POST /reject from Applied — that would
    treat a user-rejection as if they were rejecting a job they had
    already applied to, which writes feedback_log and contaminates
    the scorer's loop."""
    r = client.get("/board/applied")
    assert r.status_code == 200
    # The reject-reason <select> must not render on Applied — confirming
    # by checking that the no-reason placeholder option (only present
    # in the active select) is absent.
    assert "— No reason —" not in r.text
    # The /reject endpoint must not be wired up from any onchange handler
    # rendered on Applied.
    assert (
        "/board/jobs/" not in r.text
        or "/reject`" not in r.text
        or (
            # /reject can only appear in the company-history cell's link list,
            # never as an onchange POST target on the Applied tab. The simplest
            # invariant: no onchange that targets /reject is present.
            "htmx.ajax('POST',`/board/jobs/" not in r.text or "/reject`" not in r.text
        )
    )


def test_base_template_surfaces_htmx_response_errors(client: TestClient) -> None:
    """#361 — silent htmx failures (4xx/5xx no-swap) used to leave the row
    unchanged with no user-visible feedback. The base template wires a global
    listener (in /static/htmx_errors.js) that renders the toast container."""
    r = client.get("/board/applied")
    assert r.status_code == 200
    assert "htmx-error-toast" in r.text
    assert "/static/htmx_errors.js" in r.text
