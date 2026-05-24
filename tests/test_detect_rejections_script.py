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
    """Yields domain events only — strips the cron_started/cron_finished envelope
    that `cron_event_span` (#650) adds around `detect_rejections.main()`. Those
    are tested in `tests/test_audit_cron_event_span.py`; tests here focus on
    the script's own logic events (rejection_*).
    """
    log_path = Path(audit.LOG_PATH)
    if not log_path.exists():
        return []
    out: list[dict] = []
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") in ("cron_started", "cron_finished"):
            continue
        out.append(ev)
    return out


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


def test_ntfy_send_uses_rejection_detected_kind(harness):
    """#839: all three ntfy.send() call sites tag kind='rejection_detected'."""
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
        ("job-1", "fp-1", "Engineer", "Acme", "https://x", "web_manual", "manual_review", "applied", 0),
    )
    conn.commit()
    conn.close()

    suggestion = _make_suggestion(extracted_company="Acme")
    outcome = FetchOutcome(
        result=_RESULT_SUCCESS,
        messages=[("no-reply@us.greenhouse-mail.io", b"raw")],
        new_uid=42,
        new_uidvalidity=99,
    )

    mock_send = patch.object(detect_rejections.ntfy, "send")
    with (
        patch.object(detect_rejections, "fetch_new_messages_for_rejection_scan", return_value=outcome),
        patch.object(detect_rejections, "classify_email", return_value=suggestion),
        patch.object(detect_rejections, "match_job", return_value=MatchResult(job_id="job-1", status="matched")),
        mock_send as send_mock,
    ):
        detect_rejections.main()

    assert send_mock.call_count == 1
    assert send_mock.call_args.kwargs.get("kind") == "rejection_detected"


# ─── #586: subject/body fallback when extraction fails ───────────────────────


def _make_suggestion_with_text(
    *,
    subject: str,
    body: str,
    extracted_company: str | None = None,
    extracted_role: str | None = None,
) -> RejectionSuggestion:
    """Construct a RejectionSuggestion with explicit subject/body for fallback tests.

    The fallback path keys off `subject + body_excerpt` (lowercased), so these
    tests need control over both fields independently of the extracted_company
    that the primary corroboration path uses.
    """
    return RejectionSuggestion(
        gmail_message_id="msg-fallback-1",
        received_at="2026-05-09T00:00:00+00:00",
        sender="no-reply@us.greenhouse-mail.io",
        subject=subject,
        body_excerpt=body,
        extracted_company=extracted_company,
        extracted_role=extracted_role,
        confidence="high",
        suggested_reason="Company passed",
    )


def _seed_handled_job(harness, *, job_id: str, company: str, stage: str = "not_selected") -> None:
    """Insert a handled-stage (not_selected/rejected) job for fallback tests."""
    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
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
    conn.close()


def test_corroboration_fallback_catches_company_in_body_when_extraction_fails(harness):
    """Extraction returns None but the company name is in the email body.

    Regression for the operator's 2026-05-09 first-run backlog: an
    Anthropic-shape email had `extracted_company=None` (the "so much"
    interjection broke `_INTEREST_RE`), so the primary corroboration
    query had nothing to match against. Fallback scans subject+body
    for any handled-stage company name and corroborates if found.
    """
    _write_config(harness)
    _make_db(harness)
    _seed_handled_job(harness, job_id="job-handled-anthropic", company="Anthropic", stage="rejected")

    suggestion = _make_suggestion_with_text(
        subject="Anthropic Follow-Up for Staff Engineer",
        body="Hi, Thank you so much for your interest in Anthropic and for the time and effort.",
        extracted_company=None,  # extraction failed
    )
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
    assert corroborated_events[0]["matched_job_id"] == "job-handled-anthropic"

    # No suggestion row inserted — corroboration suppressed it.
    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM rejection_suggestions").fetchone()[0]
    conn.close()
    assert count == 0


def test_corroboration_fallback_skips_short_company_names(harness):
    """Companies shorter than _CORROBORATION_MIN_COMPANY_LEN (4) MUST NOT match.

    'IBM' or 'HP' would substring-match too liberally in body text. The
    length floor accepts false-negative corroboration on short-named
    companies in exchange for zero false-positive corroboration that
    would suppress real signals.
    """
    _write_config(harness)
    _make_db(harness)
    _seed_handled_job(harness, job_id="job-handled-ibm", company="IBM", stage="rejected")

    # Body literally contains 'IBM' but the floor blocks the match.
    suggestion = _make_suggestion_with_text(
        subject="Update on your application",
        body="Thanks for your interest at IBM. Unfortunately we have decided to move forward with other candidates.",
        extracted_company=None,
    )
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
        patch.object(detect_rejections.ntfy, "send"),
    ):
        rc = detect_rejections.main()

    assert rc == 0

    events = _read_events(harness)
    corroborated_events = [e for e in events if e["event"] == "rejection_email_corroborated"]
    assert len(corroborated_events) == 0  # IBM is too short — fallback skipped it

    # Suggestion gets persisted (operator can dismiss manually).
    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM rejection_suggestions").fetchone()[0]
    conn.close()
    assert count == 1


def test_corroboration_fallback_word_boundary_avoids_substring_false_positives(harness):
    """A 4-char company name MUST NOT match against a longer word.

    Without word-boundary anchors, 'Acme' (the company) would substring-match
    'acmestaff' or 'acmeworks' in body text and produce false-positive
    corroboration that suppresses a real signal.
    """
    _write_config(harness)
    _make_db(harness)
    _seed_handled_job(harness, job_id="job-handled-acme", company="Acme", stage="not_selected")

    # Body mentions 'acmeship' (an unrelated word containing 'acme').
    # Word-boundary regex must reject this match.
    suggestion = _make_suggestion_with_text(
        subject="Update on your application",
        body=(
            "We use acmeship and acmestaff platforms internally — "
            "but unfortunately we have decided to move forward with other "
            "candidates whose backgrounds align more closely."
        ),
        extracted_company=None,
    )
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
        patch.object(detect_rejections.ntfy, "send"),
    ):
        rc = detect_rejections.main()

    assert rc == 0

    events = _read_events(harness)
    corroborated_events = [e for e in events if e["event"] == "rejection_email_corroborated"]
    assert len(corroborated_events) == 0  # 'acme' must not substring-match 'acmeship'

    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM rejection_suggestions").fetchone()[0]
    conn.close()
    assert count == 1  # suggestion persists


def test_corroboration_fallback_no_match_persists_suggestion(harness):
    """Generic recruiter email with no handled company in subject/body → suggestion persists."""
    _write_config(harness)
    _make_db(harness)
    _seed_handled_job(harness, job_id="job-handled-anthropic", company="Anthropic", stage="rejected")

    # Body mentions no handled-stage company.
    suggestion = _make_suggestion_with_text(
        subject="Application update",
        body=(
            "Thank you for your interest. After careful consideration "
            "we have decided to move forward with other candidates."
        ),
        extracted_company=None,
    )
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
        patch.object(detect_rejections.ntfy, "send"),
    ):
        rc = detect_rejections.main()

    assert rc == 0

    events = _read_events(harness)
    corroborated_events = [e for e in events if e["event"] == "rejection_email_corroborated"]
    assert len(corroborated_events) == 0

    import sqlite3

    conn = sqlite3.connect(detect_rejections.DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM rejection_suggestions").fetchone()[0]
    conn.close()
    assert count == 1  # suggestion persisted; operator handles manually


# ─── #804: --since-days one-shot historical rescan ──────────────────────────


def test_since_days_triggers_oneshot_rescan(harness):
    """``main(since_days=30)`` → IMAP fetch called with since_days=30, distinguishing event emitted."""
    _write_config(harness)
    _make_db(harness)

    # Steady state (backlog already complete) — the realistic invocation context.
    initial_state = gmail_imap.GmailState(
        rejection_backlog_scan_complete=True,
        rejection_last_uid=500,
    )
    gmail_imap.save_state(initial_state)

    empty_outcome = FetchOutcome(
        result=_RESULT_SUCCESS,
        messages=[],
        new_uid=500,  # equal to current — no advance expected
        new_uidvalidity=42,
    )

    with patch.object(
        detect_rejections,
        "fetch_new_messages_for_rejection_scan",
        return_value=empty_outcome,
    ) as fake_fetch:
        rc = detect_rejections.main(since_days=30)

    assert rc == 0

    _, kwargs = fake_fetch.call_args
    assert kwargs.get("since_days") == 30

    events = _read_events(harness)
    event_types = [e["event"] for e in events]
    assert "rejection_oneshot_rescan_started" in event_types
    started = next(e for e in events if e["event"] == "rejection_oneshot_rescan_started")
    assert started["days"] == 30

    completed = next(e for e in events if e["event"] == "rejection_scan_completed")
    assert completed["is_backlog_run"] is False
    assert completed["is_oneshot_rescan"] is True

    # Sentinel must not be touched by one-shot mode.
    state_after = gmail_imap.load_state()
    assert state_after.rejection_backlog_scan_complete is True


def test_since_days_capped_at_max_backlog_window(harness):
    """``since_days`` over the cap is clamped to ``_MAX_BACKLOG_WINDOW_DAYS``.

    IMAP date-windowed SEARCH gets expensive with arbitrarily large windows;
    the same cap that governs the first-run backlog scan applies to the
    one-shot rescan path.
    """
    _write_config(harness)
    _make_db(harness)
    gmail_imap.save_state(gmail_imap.GmailState(rejection_backlog_scan_complete=True, rejection_last_uid=0))

    empty_outcome = FetchOutcome(result=_RESULT_SUCCESS, messages=[], new_uid=0, new_uidvalidity=42)

    with patch.object(
        detect_rejections,
        "fetch_new_messages_for_rejection_scan",
        return_value=empty_outcome,
    ) as fake_fetch:
        detect_rejections.main(since_days=999)

    _, kwargs = fake_fetch.call_args
    assert kwargs.get("since_days") == detect_rejections._MAX_BACKLOG_WINDOW_DAYS


def test_since_days_does_not_advance_uid_checkpoint_backward(harness):
    """One-shot rescan with historical UIDs must not roll the checkpoint backward.

    The existing ``new_uid > current`` guard handles this — covered here
    explicitly so a future refactor that drops the guard fails loudly.
    """
    _write_config(harness)
    _make_db(harness)
    gmail_imap.save_state(gmail_imap.GmailState(rejection_backlog_scan_complete=True, rejection_last_uid=100_000))

    # Rescanning historical mail surfaces UIDs below the current checkpoint.
    historical_outcome = FetchOutcome(result=_RESULT_SUCCESS, messages=[], new_uid=50_000, new_uidvalidity=42)

    with patch.object(
        detect_rejections,
        "fetch_new_messages_for_rejection_scan",
        return_value=historical_outcome,
    ):
        detect_rejections.main(since_days=30)

    state_after = gmail_imap.load_state()
    assert state_after.rejection_last_uid == 100_000  # unchanged
