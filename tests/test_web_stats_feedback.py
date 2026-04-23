"""Stats feedback route — /stats/feedback rendering + SQL correctness.

14e (#193). Data source is the feedback_log table (spec AC named a
feedback_stats jsonl event that was never emitted — the table is the real
source). feedback_log.created_at uses SQLite's datetime('now') default, which
writes naïve-UTC "YYYY-MM-DD HH:MM:SS" — same format as audit_log.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


def _ts(days_ago: int, hour: int = 12) -> str:
    dt = datetime.now(UTC) - timedelta(days=days_ago)
    dt = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    # jobs + audit_log exist because get_db is shared with funnel; feedback_log
    # is the one we actually query here.
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT)")
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE feedback_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, title TEXT NOT NULL, "
        "company TEXT NOT NULL, relevance_score INTEGER, reject_reason TEXT NOT NULL, "
        "jd_excerpt TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))"
    )
    # Seed feedback_log with known entries across the window.
    # Within the 7-day this-week window:
    entries = [
        ("j1", "Skills Mismatch", _ts(0)),
        ("j2", "Skills Mismatch", _ts(1)),
        ("j3", "Too Senior", _ts(2)),
        ("j4", "Comp Too Low", _ts(3)),
        # Still in 28-day window but outside this-week:
        ("j5", "Geography/Onsite", _ts(10)),
        ("j6", "Geography/Onsite", _ts(15)),
        ("j7", "Other", _ts(20)),
        # Legacy reject_reason not in canonical list — must appear as an extra column:
        ("j8", "Legacy Reason X", _ts(5)),
        # Outside the 28-day window — must NOT be counted:
        ("j9", "Too Senior", _ts(45)),
    ]
    for job_id, reason, created in entries:
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, title, company, stage) VALUES (?, ?, ?, ?, 'rejected')",
            (job_id, f"fp_{job_id}", f"Title {job_id}", f"Co {job_id}"),
        )
        conn.execute(
            "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason, created_at) "
            "VALUES (?, ?, ?, 7, ?, ?)",
            (job_id, f"Title {job_id}", f"Co {job_id}", reason, created),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db))


def test_feedback_renders(client: TestClient) -> None:
    r = client.get("/stats/feedback")
    assert r.status_code == 200
    assert 'id="feedback-chart"' in r.text
    assert 'id="feedback-chart-data"' in r.text
    # Canonical reasons must appear as summary-grid labels even when absent.
    for reason in ("Too Senior", "Skills Mismatch", "Low Fit Score"):
        assert reason in r.text


def test_this_week_rollup_counts(client: TestClient) -> None:
    """This-week summary shows only reasons with >0 in the last 7 days."""
    r = client.get("/stats/feedback")
    assert r.status_code == 200
    # This-week reasons: Skills Mismatch (2), Too Senior (1), Comp Too Low (1),
    # Legacy Reason X (1) at day 5. Total: 5.
    # Section title contains "5 total" somewhere in the this-week block.
    week_anchor = r.text.index("This week")
    this_week_snippet = r.text[week_anchor : week_anchor + 2000]
    assert "5 total" in this_week_snippet


def test_window_excludes_out_of_range_rows(client: TestClient) -> None:
    """Entry from 45 days ago must not appear in the 28-day window totals."""
    r = client.get("/stats/feedback")
    assert r.status_code == 200
    # 28-day total: in-window entries = j1..j8 = 8.
    # Top-level window total rendered near "28-day totals" header.
    window_anchor = r.text.index("28-day totals")
    window_snippet = r.text[window_anchor : window_anchor + 2000]
    assert "8 total" in window_snippet


def test_legacy_reasons_render_after_canonical(client: TestClient) -> None:
    """Non-canonical reject_reason values appear as extra columns, after canonical."""
    r = client.get("/stats/feedback")
    assert r.status_code == 200
    assert "Legacy Reason X" in r.text
    # Canonical "Too Senior" must appear before "Legacy Reason X" in source order.
    assert r.text.index("Too Senior") < r.text.index("Legacy Reason X")


def test_empty_feedback_log_renders_zero_state(tmp_path: Path) -> None:
    """No rows in window → renders 200 with a zero-state message, not a crash."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT)")
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE feedback_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, title TEXT NOT NULL, "
        "company TEXT NOT NULL, relevance_score INTEGER, reject_reason TEXT NOT NULL, "
        "jd_excerpt TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    client = TestClient(create_app(companies_root=companies, db_path=db))
    r = client.get("/stats/feedback")
    assert r.status_code == 200
    assert "No rejections logged" in r.text
