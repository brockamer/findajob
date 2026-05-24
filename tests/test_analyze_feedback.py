"""Characterization test for ``findajob.analyze_feedback.analyze``.

Builds a minimal feedback_log + jobs schema in :memory:, populates it
with a small rejection corpus, and asserts the output dict has the
documented shape. AC #3 of #558.

Behavior preserved across the M3+ extraction (pre-#558 the same logic
lived in ``scripts/analyze_feedback.py``).
"""

from __future__ import annotations

import sqlite3

import pytest

from findajob.analyze_feedback import _prefilter_candidates, analyze, format_report, main


@pytest.fixture
def conn(monkeypatch):
    """In-memory DB with feedback_log + jobs at the analyze-required shape."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE feedback_log (
            id INTEGER PRIMARY KEY,
            job_id TEXT,
            title TEXT,
            company TEXT,
            relevance_score INTEGER,
            reject_reason TEXT
        );
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            source TEXT,
            url TEXT,
            stage TEXT,
            dupe_of TEXT DEFAULT '',
            relevance_score INTEGER
        );
        """
    )
    # Stub load_reject_reasons so _prefilter_candidates can run without
    # the real config file. Both the canonical (full) reasons set AND
    # the title-signal-reasons subset are needed.
    import findajob.analyze_feedback as af

    monkeypatch.setattr(
        af,
        "load_reject_reasons",
        lambda: (["Low Fit Score", "Already Applied", "Stale/Closed"], frozenset({"Low Fit Score"})),
    )
    return conn


def test_analyze_returns_no_feedback_error_when_empty(conn):
    """Empty feedback_log → ``error`` field set, early-return shape."""
    result = analyze(conn)
    assert result["total_feedback"] == 0
    assert result.get("error") == "No feedback entries yet"


def test_analyze_with_rejections_returns_documented_shape(conn):
    """Populated DB → all 8 documented fields present in output."""
    # 3 rejections, mix of reasons + scores
    conn.execute(
        "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason) VALUES "
        "('j1', 'Senior Data Center Manager', 'Acme', 9, 'Low Fit Score'),"
        "('j2', 'Junior DevOps Engineer', 'Beta Corp', 8, 'Low Fit Score'),"
        "('j3', 'Software Engineer III', 'Gamma', 4, 'Already Applied')"
    )
    # 1 applied job + 1 rejected high-score job for keyword-signal computation
    conn.execute(
        "INSERT INTO jobs (id, title, company, source, url, stage, relevance_score, dupe_of) VALUES "
        "('j10', 'Senior NPI Manager', 'AppliedCo', 'jsearch', 'http://x', 'applied', 8, ''),"
        "('j11', 'Junior DevOps Engineer', 'RejectCo', 'jsearch', 'http://y', 'rejected', 8, '')"
    )
    conn.commit()

    result = analyze(conn)

    # Documented top-level fields
    assert result["total_feedback"] == 3
    assert isinstance(result["by_reason"], list)
    assert result["false_positives"] == 2  # score >= 8: j1, j2
    assert isinstance(result["fp_pct"], float)
    assert isinstance(result["fp_by_reason"], list)
    assert isinstance(result["score_distribution"], list)
    assert isinstance(result["keyword_signals"], list)
    assert isinstance(result["company_fp_counts"], list)
    assert isinstance(result["fp_by_source"], list)
    assert isinstance(result["source_fp_rates"], list)
    assert isinstance(result["fp_title_word_freq"], list)
    assert isinstance(result["prefilter_candidates"], list)
    assert "n_applied_jobs" in result
    assert "n_rejected_high_score" in result


def test_format_report_renders_without_error(conn):
    """``format_report`` produces a non-empty string for both empty + populated cases."""
    empty_report = format_report(analyze(conn))
    assert "No feedback entries yet" in empty_report

    conn.execute(
        "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason) VALUES "
        "('j1', 'Senior Data Center Manager', 'Acme', 9, 'Low Fit Score')"
    )
    conn.commit()

    populated_report = format_report(analyze(conn))
    assert "JSP FEEDBACK LOOP ANALYSIS" in populated_report
    assert "REJECTION BREAKDOWN" in populated_report
    assert "FALSE POSITIVES" in populated_report


def test_prefilter_candidates_filters_title_signal_reasons_only(monkeypatch):
    """``_prefilter_candidates`` ignores rows whose reject_reason isn't in title_signal_reasons.

    Stub sets title_signal_reasons={'Low Fit Score'}. A 'Stale/Closed'
    rejection should not contribute to the n-gram count even if it would
    otherwise meet the recurrence threshold.
    """
    import findajob.analyze_feedback as af

    monkeypatch.setattr(
        af,
        "load_reject_reasons",
        lambda: (["Low Fit Score", "Stale/Closed"], frozenset({"Low Fit Score"})),
    )

    # Three rejections sharing the n-gram "junior devops" — but two are
    # 'Stale/Closed' (not a title-signal reason), so the recurrence count
    # for filtering purposes is 1 (below min_recurrences=2).
    rejected = [
        {"title": "Junior DevOps Engineer", "reject_reason": "Low Fit Score"},
        {"title": "Junior DevOps Specialist", "reject_reason": "Stale/Closed"},
        {"title": "Junior DevOps Lead", "reject_reason": "Stale/Closed"},
    ]
    applied: list[dict] = []
    candidates = _prefilter_candidates(rejected, applied, min_recurrences=2)
    # Should be empty — only 1 row contributes to the count after filter.
    assert candidates == []


def test_main_notify_calls_ntfy_send_with_feedback_review_kind(tmp_path, monkeypatch):
    """--notify flag routes through ntfy.send() with kind='feedback_review' (#838)."""
    import findajob.analyze_feedback as af

    db_path = str(tmp_path / "pipeline.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE feedback_log (
            id INTEGER PRIMARY KEY, job_id TEXT, title TEXT, company TEXT,
            relevance_score INTEGER, reject_reason TEXT
        );
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, title TEXT, company TEXT, source TEXT,
            url TEXT, stage TEXT, dupe_of TEXT DEFAULT '', relevance_score INTEGER
        );
        INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason)
            VALUES ('j1', 'Engineer', 'Acme', 7, 'Low Fit Score');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(af, "DB_PATH", db_path)
    monkeypatch.setattr(
        af,
        "load_reject_reasons",
        lambda: (["Low Fit Score"], frozenset({"Low Fit Score"})),
    )
    monkeypatch.setattr("sys.argv", ["analyze_feedback.py", "--notify"])

    calls: list[dict] = []

    def fake_send(*, title, body, tags, kind):
        calls.append({"title": title, "body": body, "tags": tags, "kind": kind})

    monkeypatch.setattr("findajob.notifications.ntfy.send", fake_send)

    main()

    assert len(calls) == 1
    assert calls[0]["kind"] == "feedback_review"
    assert calls[0]["title"] == "JSP Feedback Analysis"
    assert calls[0]["tags"] == "magnifying_glass"
    assert "rejections" in calls[0]["body"]
