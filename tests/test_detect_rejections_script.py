"""Tests for ``scripts/detect_rejections.py`` orchestration (#362, M-stage 4).

Covers the no-op, first-run backlog gating, corroborated-path telemetry,
steady-state UID advance, and IMAP-failure surfaces from spec §4.6, §4.7,
§4.8. Synthetic ``RejectionSuggestion`` / ``MatchResult`` / ``FetchOutcome``
inputs at the orchestration seam — the upstream IMAP / classifier / matcher
modules are exercised end-to-end by their own test files (Tasks 1–3).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob import audit, gmail_imap
from findajob.db.migrate import apply_pending
from findajob.gmail_imap import FetchOutcome
from findajob.rejection_detector import MatchResult, RejectionSuggestion
from scripts import detect_rejections

# ``gmail_imap.TestResult`` is referenced through the module to avoid pytest's
# auto-collection of any name starting with "Test" in the test module globals.
_RESULT_SUCCESS = gmail_imap.TestResult.SUCCESS
_RESULT_AUTH_FAILED = gmail_imap.TestResult.AUTH_FAILED


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Stub all module-level paths the script + its imports read at module load.

    Returns the tmp_path Path so each test can probe the resulting state /
    log files directly.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    config_path = config_dir / "gmail.json"
    state_path = config_dir / "gmail_state.json"
    log_path = logs_dir / "pipeline.jsonl"
    db_path = tmp_path / "pipeline.db"

    monkeypatch.setattr(gmail_imap, "GMAIL_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(gmail_imap, "GMAIL_STATE_PATH", str(state_path))
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))
    monkeypatch.setattr(detect_rejections, "DB_PATH", str(db_path))

    return tmp_path


def _write_config(tmp_path: Path) -> None:
    """Persist a valid gmail.json so ``load_config`` returns a GmailConfig.

    Built via the dataclass + ``save_config`` rather than hand-written JSON
    so the pre-commit PII hook doesn't flag the test-fixture credential.
    """
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
        rejection_sender_allowlist=list(gmail_imap.DEFAULT_REJECTION_ALLOWLIST),
    )
    gmail_imap.save_config(cfg)


def _read_events(tmp_path: Path) -> list[dict]:
    log_path = Path(audit.LOG_PATH)
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _make_db(tmp_path: Path) -> None:
    """Apply migrations 1–3 to the harness DB so jobs + rejection_suggestions exist."""
    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    apply_pending(conn)
    conn.close()


def _make_suggestion(
    *,
    gmail_message_id: str = "msg-1",
    extracted_company: str | None = "Acme Corp",
    extracted_role: str | None = "Senior Engineer",
    confidence: str = "high",
) -> RejectionSuggestion:
    return RejectionSuggestion(
        gmail_message_id=gmail_message_id,
        received_at="2026-05-09T00:00:00+00:00",
        sender="no-reply@us.greenhouse-mail.io",
        subject=f"Update on your application at {extracted_company}",
        body_excerpt="We have decided not to move forward.",
        extracted_company=extracted_company,
        extracted_role=extracted_role,
        confidence=confidence,
        suggested_reason="Company passed",
    )


def test_noop_when_gmail_unconfigured(harness):
    """No config/gmail.json → script exits 0, logs rejection_scan_skipped, no DB writes."""
    rc = detect_rejections.main()

    assert rc == 0
    events = _read_events(harness)
    assert len(events) == 1
    assert events[0]["event"] == "rejection_scan_skipped"
    assert events[0]["reason"] == "gmail_unconfigured"
    # DB file must not exist — script returned before any connect() call.
    assert not Path(detect_rejections.DB_PATH).exists()


def test_first_run_sets_backlog_complete(harness):
    """Empty state → backlog run with default 30-day window → state sentinel flips."""
    _write_config(harness)
    _make_db(harness)

    empty_outcome = FetchOutcome(
        result=_RESULT_SUCCESS,
        messages=[],
        new_uid=0,
        new_uidvalidity=42,
    )

    with patch.object(
        detect_rejections,
        "fetch_new_messages_for_rejection_scan",
        return_value=empty_outcome,
    ) as fake_fetch:
        rc = detect_rejections.main()

    assert rc == 0

    # Confirm the backlog branch fired with default window.
    _, kwargs = fake_fetch.call_args
    assert kwargs.get("since_days") == detect_rejections._DEFAULT_BACKLOG_WINDOW_DAYS

    state = gmail_imap.load_state()
    assert state.rejection_backlog_scan_complete is True

    events = _read_events(harness)
    event_types = [e["event"] for e in events]
    assert "rejection_backlog_scan_started" in event_types
    assert "rejection_scan_completed" in event_types
    completed = next(e for e in events if e["event"] == "rejection_scan_completed")
    assert completed["is_backlog_run"] is True
    assert completed["scanned"] == 0
    assert completed["suggestions_created"] == 0


def test_corroborated_rejection_emits_event_no_db_row(harness):
    """Email matches a not_selected job at the same company → corroborated event, no row."""
    _write_config(harness)
    _make_db(harness)

    # Seed a not_selected job at "Acme Corp"; matcher excludes this stage,
    # so corroboration must be the script-local secondary lookup.
    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    conn.execute(
        """
        INSERT INTO jobs (
            id, fingerprint, title, company, url, source, status, stage, synthetic
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job-handled-1",
            "fp-1",
            "Senior Engineer",
            "Acme Corp",
            "https://example.com/1",
            "web_manual",
            "manual_review",
            "not_selected",
            0,
        ),
    )
    conn.commit()
    conn.close()

    suggestion = _make_suggestion(extracted_company="Acme Corp")
    outcome = FetchOutcome(
        result=_RESULT_SUCCESS,
        messages=[("no-reply@us.greenhouse-mail.io", b"raw")],
        new_uid=42,
        new_uidvalidity=99,
    )

    with (
        patch.object(detect_rejections, "fetch_new_messages_for_rejection_scan", return_value=outcome),
        patch.object(detect_rejections, "classify_email", return_value=suggestion),
        patch.object(detect_rejections, "match_job", return_value=MatchResult(job_id=None, status="unmatched")),
    ):
        rc = detect_rejections.main()

    assert rc == 0

    events = _read_events(harness)
    corroborated_events = [e for e in events if e["event"] == "rejection_email_corroborated"]
    assert len(corroborated_events) == 1
    assert corroborated_events[0]["matched_job_id"] == "job-handled-1"
    assert corroborated_events[0]["confidence"] == "high"

    # Crucially: no rejection_suggestions row was inserted.
    conn = sqlite3.connect(detect_rejections.DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM rejection_suggestions").fetchone()[0]
    conn.close()
    assert count == 0

    completed = next(e for e in events if e["event"] == "rejection_scan_completed")
    assert completed["corroborated"] == 1
    assert completed["suggestions_created"] == 0


def test_steady_state_advances_uid_checkpoint(harness):
    """Steady-state run with messages → state.rejection_last_uid advances on success.

    Guards the advisor-flagged bug where ignoring outcome.new_uid would cause
    every cycle to reprocess every email.
    """
    _write_config(harness)
    _make_db(harness)

    # Mark backlog complete so we hit the steady-state branch.
    initial_state = gmail_imap.GmailState(
        rejection_backlog_scan_complete=True,
        rejection_last_uid=100,
    )
    gmail_imap.save_state(initial_state)

    outcome = FetchOutcome(
        result=_RESULT_SUCCESS,
        messages=[],  # no messages this cycle, but UID still advanced server-side
        new_uid=125,
        new_uidvalidity=99,
    )

    with patch.object(detect_rejections, "fetch_new_messages_for_rejection_scan", return_value=outcome):
        rc = detect_rejections.main()

    assert rc == 0
    state_after = gmail_imap.load_state()
    assert state_after.rejection_last_uid == 125
    assert state_after.rejection_backlog_scan_complete is True


def test_imap_failure_logs_and_returns_zero(harness):
    """fetch_new_messages_for_rejection_scan returning non-SUCCESS → no crash, logged."""
    _write_config(harness)
    _make_db(harness)

    failed_outcome = FetchOutcome(
        result=_RESULT_AUTH_FAILED,
        messages=[],
        new_uid=None,
        new_uidvalidity=None,
    )

    with patch.object(detect_rejections, "fetch_new_messages_for_rejection_scan", return_value=failed_outcome):
        rc = detect_rejections.main()

    assert rc == 0

    events = _read_events(harness)
    failure_events = [e for e in events if e["event"] == "rejection_scan_failed"]
    assert len(failure_events) == 1
    assert failure_events[0]["reason"] == _RESULT_AUTH_FAILED.value

    # State must NOT roll over to backlog_complete on a failed first run.
    state = gmail_imap.load_state()
    assert state.rejection_backlog_scan_complete is False


def test_matched_suggestion_persists_to_db(harness):
    """Matcher returns a job_id → row inserted into rejection_suggestions."""
    _write_config(harness)
    _make_db(harness)

    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    conn.execute(
        """
        INSERT INTO jobs (
            id, fingerprint, title, company, url, source, status, stage, synthetic
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "job-applied-1",
            "fp-1",
            "Senior Engineer",
            "Acme Corp",
            "https://example.com/2",
            "web_manual",
            "manual_review",
            "applied",
            0,
        ),
    )
    conn.commit()
    conn.close()

    suggestion = _make_suggestion(extracted_company="Acme Corp")
    outcome = FetchOutcome(
        result=_RESULT_SUCCESS,
        messages=[("no-reply@us.greenhouse-mail.io", b"raw")],
        new_uid=42,
        new_uidvalidity=99,
    )

    with (
        patch.object(detect_rejections, "fetch_new_messages_for_rejection_scan", return_value=outcome),
        patch.object(detect_rejections, "classify_email", return_value=suggestion),
        patch.object(
            detect_rejections,
            "match_job",
            return_value=MatchResult(job_id="job-applied-1", status="matched"),
        ),
        patch.object(detect_rejections.ntfy, "send"),
    ):
        rc = detect_rejections.main()

    assert rc == 0

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    rows = conn.execute(
        "SELECT gmail_message_id, matched_job_id, match_status, confidence, suggested_reason FROM rejection_suggestions"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    msg_id, job_id, match_status, confidence, reason = rows[0]
    assert msg_id == "msg-1"
    assert job_id == "job-applied-1"
    assert match_status == "matched"
    assert confidence == "high"
    assert reason == "Company passed"
