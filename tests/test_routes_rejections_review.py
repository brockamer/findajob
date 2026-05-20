"""Route tests for the /board/rejections-review/ surface (#362, M-stage 5).

Covers index render, confirm/dismiss/reattribute happy paths, idempotency on
double-clicks, the 409 guard against confirming an unmatched suggestion,
and the dashboard widget partial. Built on the same ``apply_pending``-based
fixture other route tests use, so the production schema is exercised.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from findajob.db.migrate import apply_pending
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _make_client(tmp_path: Path) -> tuple[sqlite3.Connection, TestClient]:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_pending(conn)
    conn.commit()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))
    return conn, client


def _seed_job(conn: sqlite3.Connection, *, job_id: str, stage: str = "applied", company: str = "Acme Corp") -> None:
    conn.execute(
        """
        INSERT INTO jobs (
            id, fingerprint, title, company, url, source, status, stage, synthetic
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            f"fp-{job_id}",
            "Senior Engineer",
            company,
            f"https://example.com/{job_id}",
            "web_manual",
            "manual_review",
            stage,
            0,
        ),
    )
    conn.commit()


def _seed_suggestion(
    conn: sqlite3.Connection,
    *,
    suggestion_id: int = 1,
    matched_job_id: str | None = "job-1",
    confidence: str = "high",
    user_action: str = "pending",
    suggested_reason: str = "Company passed",
    match_status: str = "matched",
) -> None:
    conn.execute(
        """
        INSERT INTO rejection_suggestions (
            id, gmail_message_id, received_at, sender, subject, body_excerpt,
            extracted_company, extracted_role, matched_job_id, match_status,
            confidence, suggested_reason, user_action
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            suggestion_id,
            f"gmail-msg-{suggestion_id}",
            "2026-05-09T10:00:00+00:00",
            "no-reply@us.greenhouse-mail.io",
            "Update on your application at Acme Corp",
            "We have decided not to move forward.",
            "Acme Corp",
            "Senior Engineer",
            matched_job_id,
            match_status,
            confidence,
            suggested_reason,
            user_action,
        ),
    )
    conn.commit()


# ── index ────────────────────────────────────────────────────────────────────


def test_index_renders_pending_only(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-1")
    _seed_job(conn, job_id="job-2")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id="job-1", user_action="pending")
    _seed_suggestion(conn, suggestion_id=2, matched_job_id="job-2", user_action="confirmed")
    _seed_suggestion(conn, suggestion_id=3, matched_job_id=None, user_action="dismissed", match_status="unmatched")

    resp = client.get("/board/rejections-review/")
    assert resp.status_code == 200
    assert "Rejection review queue" in resp.text
    assert "1 pending" in resp.text
    # Pending row visible; confirmed/dismissed rows excluded.
    assert "suggestion-1" in resp.text
    assert "suggestion-2" not in resp.text
    assert "suggestion-3" not in resp.text


def test_index_empty_state(tmp_path):
    _, client = _make_client(tmp_path)
    resp = client.get("/board/rejections-review/")
    assert resp.status_code == 200
    assert "No pending suggestions" in resp.text


# ── reattribute dropdown ─────────────────────────────────────────────────────


def test_index_renders_reattribute_dropdown_with_legal_targets(tmp_path):
    """Reattribute affordance is a <select> with applied/interview/offer rows only.

    Regression for #661: prior placeholder asked the operator to paste
    `jobs.id`, but that UUID is never UI-visible. The dropdown both removes
    the unusable freeform input and constrains targets to stages the POST
    handler will accept.
    """
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-applied", stage="applied", company="LegalCo")
    _seed_job(conn, job_id="job-interview", stage="interview", company="LegalCo")
    _seed_job(conn, job_id="job-offer", stage="offer", company="LegalCo")
    _seed_job(conn, job_id="job-scored", stage="scored", company="ScoredCo")
    _seed_job(conn, job_id="job-rejected", stage="rejected", company="RejectedCo")
    _seed_job(conn, job_id="job-not-selected", stage="not_selected", company="NotSelCo")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id=None, match_status="unmatched")

    resp = client.get("/board/rejections-review/")
    assert resp.status_code == 200
    assert '<select name="job_id"' in resp.text
    # Legal targets present as options.
    assert 'value="job-applied"' in resp.text
    assert 'value="job-interview"' in resp.text
    assert 'value="job-offer"' in resp.text
    # Non-legal stages absent.
    assert 'value="job-scored"' not in resp.text
    assert 'value="job-rejected"' not in resp.text
    assert 'value="job-not-selected"' not in resp.text
    # Old freeform input + placeholder are gone.
    assert "paste jobs.id" not in resp.text
    assert 'type="text" name="job_id"' not in resp.text


def test_index_reattribute_empty_state_when_no_legal_targets(tmp_path):
    """With zero applied/interview/offer rows, render a non-actionable hint."""
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-scored", stage="scored")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id=None, match_status="unmatched")

    resp = client.get("/board/rejections-review/")
    assert resp.status_code == 200
    assert '<select name="job_id"' not in resp.text
    assert "No applied/interview/offer jobs to reattribute to" in resp.text


def test_index_reattribute_targets_sorted_by_recency(tmp_path):
    """Most-recently-transitioned legal target appears first in the dropdown."""
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-old", stage="applied", company="OldCo")
    _seed_job(conn, job_id="job-new", stage="applied", company="NewCo")
    conn.execute("UPDATE jobs SET stage_updated = '2026-01-01 00:00:00' WHERE id = 'job-old'")
    conn.execute("UPDATE jobs SET stage_updated = '2026-05-20 00:00:00' WHERE id = 'job-new'")
    conn.commit()
    _seed_suggestion(conn, suggestion_id=1, matched_job_id=None, match_status="unmatched")

    resp = client.get("/board/rejections-review/")
    assert resp.status_code == 200
    # job-new should appear before job-old in option order.
    new_pos = resp.text.index('value="job-new"')
    old_pos = resp.text.index('value="job-old"')
    assert new_pos < old_pos


# ── confirm ──────────────────────────────────────────────────────────────────


def test_confirm_marks_job_not_selected_and_audits_with_detector_tag(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-1", stage="applied")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id="job-1")

    resp = client.post("/board/rejections-review/1/confirm", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert resp.text == ""  # HTMX swap-out

    job_row = conn.execute("SELECT stage, reject_reason FROM jobs WHERE id='job-1'").fetchone()
    assert job_row["stage"] == "not_selected"
    assert job_row["reject_reason"] == "Company passed"

    sug_row = conn.execute("SELECT user_action, user_action_at FROM rejection_suggestions WHERE id=1").fetchone()
    assert sug_row["user_action"] == "confirmed"
    assert sug_row["user_action_at"] is not None

    audits = conn.execute("SELECT changed_by FROM audit_log WHERE job_id='job-1' ORDER BY id").fetchall()
    assert len(audits) == 2
    assert all(a["changed_by"] == "gmail_rejection_detector" for a in audits)


def test_confirm_409_when_unmatched(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_suggestion(conn, suggestion_id=1, matched_job_id=None, match_status="unmatched")
    resp = client.post("/board/rejections-review/1/confirm", headers={"HX-Request": "true"})
    assert resp.status_code == 409


def test_confirm_409_when_job_stage_outside_applied_window(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-1", stage="scored")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id="job-1")
    resp = client.post("/board/rejections-review/1/confirm", headers={"HX-Request": "true"})
    assert resp.status_code == 409


def test_confirm_idempotent_after_first_apply(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-1", stage="applied")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id="job-1")

    r1 = client.post("/board/rejections-review/1/confirm", headers={"HX-Request": "true"})
    r2 = client.post("/board/rejections-review/1/confirm", headers={"HX-Request": "true"})
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Second call must NOT re-apply handle_not_selected (no extra audit rows).
    audits = conn.execute("SELECT COUNT(*) FROM audit_log WHERE job_id='job-1'").fetchone()[0]
    assert audits == 2


def test_confirm_404_unknown_suggestion(tmp_path):
    _, client = _make_client(tmp_path)
    resp = client.post("/board/rejections-review/999/confirm", headers={"HX-Request": "true"})
    assert resp.status_code == 404


def test_confirm_non_htmx_redirects(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-1", stage="applied")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id="job-1")
    resp = client.post("/board/rejections-review/1/confirm", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/board/rejections-review/"


# ── dismiss ──────────────────────────────────────────────────────────────────


def test_dismiss_marks_user_action_and_leaves_job_alone(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-1", stage="applied")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id="job-1")

    resp = client.post("/board/rejections-review/1/dismiss", headers={"HX-Request": "true"})
    assert resp.status_code == 200

    sug = conn.execute("SELECT user_action FROM rejection_suggestions WHERE id=1").fetchone()
    assert sug["user_action"] == "dismissed"

    # Job stage UNTOUCHED — dismiss is "this isn't a rejection".
    job = conn.execute("SELECT stage FROM jobs WHERE id='job-1'").fetchone()
    assert job["stage"] == "applied"

    # No audit rows from a dismiss.
    audits = conn.execute("SELECT COUNT(*) FROM audit_log WHERE job_id='job-1'").fetchone()[0]
    assert audits == 0


# ── reattribute ──────────────────────────────────────────────────────────────


def test_reattribute_applies_to_chosen_job_and_records_user_chose(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_job(conn, job_id="job-original", stage="applied", company="Acme Corp")
    _seed_job(conn, job_id="job-correct", stage="applied", company="Acme Corp")
    _seed_suggestion(conn, suggestion_id=1, matched_job_id="job-original")

    resp = client.post(
        "/board/rejections-review/1/reattribute",
        data={"job_id": "job-correct"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200

    sug = conn.execute("SELECT user_action, user_chose_job_id FROM rejection_suggestions WHERE id=1").fetchone()
    assert sug["user_action"] == "reassigned"
    assert sug["user_chose_job_id"] == "job-correct"

    # Operator-chosen job got transitioned, original did not.
    correct = conn.execute("SELECT stage FROM jobs WHERE id='job-correct'").fetchone()
    original = conn.execute("SELECT stage FROM jobs WHERE id='job-original'").fetchone()
    assert correct["stage"] == "not_selected"
    assert original["stage"] == "applied"

    # Audit rows tagged with the detector.
    audits = conn.execute("SELECT changed_by FROM audit_log WHERE job_id='job-correct'").fetchall()
    assert len(audits) == 2
    assert all(a["changed_by"] == "gmail_rejection_detector" for a in audits)


def test_reattribute_404_unknown_target_job(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_suggestion(conn, suggestion_id=1, matched_job_id=None, match_status="unmatched")
    resp = client.post(
        "/board/rejections-review/1/reattribute",
        data={"job_id": "does-not-exist"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 404


# ── widget ───────────────────────────────────────────────────────────────────


def test_widget_renders_when_pending_present(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_suggestion(conn, suggestion_id=1, matched_job_id=None, match_status="unmatched")
    resp = client.get("/board/rejections-review/widget")
    assert resp.status_code == 200
    assert "Rejection emails detected" in resp.text
    assert "1 pending review" in resp.text


def test_widget_empty_when_zero_pending(tmp_path):
    _, client = _make_client(tmp_path)
    resp = client.get("/board/rejections-review/widget")
    assert resp.status_code == 200
    assert "Rejection emails detected" not in resp.text


def test_dashboard_widget_inline_when_pending(tmp_path):
    conn, client = _make_client(tmp_path)
    _seed_suggestion(conn, suggestion_id=1, matched_job_id=None, match_status="unmatched")
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "Rejection emails detected" in resp.text
    assert "1 pending review" in resp.text


def test_dashboard_widget_absent_when_zero(tmp_path):
    _, client = _make_client(tmp_path)
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "Rejection emails detected" not in resp.text
