"""Tests for the onboarding feed-check step (#984).

After the interview finalizes and writes config/feed_urls.txt, this step probes
every emitted ATS board URL for liveness and surfaces the ones that couldn't be
verified — so a non-technical user never starts with a silently-leaking funnel.
It is non-blocking: the Continue link is always present, regardless of the probe.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob.fetchers.feed_probe import FeedProbeResult
from findajob.web.app import create_app

_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    created_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE onboarding_sessions (
    id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL,
    captured_blocks_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    last_turn_at TEXT NOT NULL,
    completed_at TEXT,
    error_state TEXT
);
"""

_PROBE = "findajob.web.routes.onboarding_feed_check.probe_feed_urls"


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.close()
    (tmp_path / "config" / "active_sources.txt").write_text("greenhouse\n")
    return tmp_path


@pytest.fixture
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _res(
    slug: str,
    status: str,
    *,
    kind: str = "greenhouse",
    http: int | None = None,
    reason: str = "",
    company: str | None = None,
    ok: bool = True,
) -> FeedProbeResult:
    return FeedProbeResult(
        line=f"https://x/{slug}",
        kind=kind,
        slug=slug,
        status=status,  # type: ignore[arg-type]
        http_status=http,
        reason=reason,
        company=company,
        company_name_ok=ok,
    )


def test_get_page_renders_with_always_present_continue_to_timezone(client: TestClient) -> None:
    """The page renders instantly with a Continue link to the timezone step —
    the non-blocking guarantee. Results load asynchronously, so a slow probe
    never gates the page or the Continue affordance.
    """
    resp = client.get("/onboarding/feed-check/sess123/")

    assert resp.status_code == 200
    assert "/onboarding/timezone/sess123/" in resp.text
    # results panel is loaded async from the dedicated results endpoint
    assert "/onboarding/feed-check/sess123/results" in resp.text


def test_get_page_propagates_voice_redact_flag_onward(client: TestClient) -> None:
    """voice_redact_failed threads finalize -> feed-check -> timezone untouched."""
    resp = client.get("/onboarding/feed-check/sess123/?voice_redact_failed=1")

    assert resp.status_code == 200
    assert "/onboarding/timezone/sess123/?voice_redact_failed=1" in resp.text


def test_results_surface_dead_feed_with_company_and_reason(base_root: Path, client: TestClient) -> None:
    (base_root / "config" / "feed_urls.txt").write_text(
        "https://boards.greenhouse.io/liveco  # LiveCo\nhttps://jobs.ashbyhq.com/deadco  # DeadCo\n"
    )
    fake = [
        _res("liveco", "live", http=200, company="LiveCo"),
        _res(
            "deadco",
            "dead",
            kind="ashby",
            http=404,
            reason="404 — slug 'deadco' isn't a valid board.",
            company="DeadCo",
        ),
    ]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "couldn't be verified" in resp.text.lower()
    assert "DeadCo" in resp.text
    assert "404" in resp.text


def test_results_all_live_shows_clean_state_no_false_alarm(base_root: Path, client: TestClient) -> None:
    (base_root / "config" / "feed_urls.txt").write_text("https://boards.greenhouse.io/liveco  # LiveCo\n")
    fake = [_res("liveco", "live", http=200, company="LiveCo")]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "couldn't be verified" not in resp.text.lower()


def test_results_missing_feed_urls_is_graceful(client: TestClient) -> None:
    """No feed_urls.txt (user emitted no board feeds) — no crash, nothing to verify."""
    resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200


def test_results_escape_user_controlled_feed_content(base_root: Path, client: TestClient) -> None:
    """company/slug/line come from the user's feed_urls.txt and render in the
    results panel. They must be HTML-escaped (Jinja autoescape) — a crafted
    inline comment must never inject markup into the onboarding page.
    """
    fake = [
        _res(
            "x",
            "dead",
            kind="ashby",
            http=404,
            reason="404 bad",
            company="<script>alert(1)</script>",
            ok=False,
        )
    ]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_results_singular_live_feed_uses_singular_verb(base_root: Path, client: TestClient) -> None:
    """Exactly one live feed must read 'All 1 job feed is live' — subject/verb
    agreement. (Regression for the hardcoded plural verb.)
    """
    fake = [_res("liveco", "live", http=200, company="LiveCo")]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "All 1 job feed is live" in resp.text


def test_results_unsupported_line_renders_with_url_fallback(base_root: Path, client: TestClient) -> None:
    """An unsupported result has no company/slug — the row must fall back to the
    raw line so it's still surfaced (flagged, not blank)."""
    fake = [
        FeedProbeResult(
            line="https://example.com/jobs",
            kind=None,
            slug=None,
            status="unsupported",
            http_status=None,
            reason="Unsupported ATS — findajob probes Greenhouse, Ashby, and Lever boards.",
            company=None,
            company_name_ok=True,
        )
    ]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "https://example.com/jobs" in resp.text
    assert "unsupported" in resp.text.lower()


def test_results_comments_only_file_is_graceful(base_root: Path, client: TestClient) -> None:
    """A feed_urls.txt with only headings/blank lines yields nothing to probe —
    the 'nothing to verify' state, not a crash. Real probe path (no mock)."""
    (base_root / "config" / "feed_urls.txt").write_text("# ===== Greenhouse =====\n\n  \n")

    resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "to verify" in resp.text.lower()


def test_results_multiple_problems_all_rendered(base_root: Path, client: TestClient) -> None:
    fake = [
        _res("deadco", "dead", kind="ashby", http=404, reason="404 bad", company="DeadCo"),
        _res("flakyco", "unreachable", kind="lever", http=503, reason="HTTP 503 transient", company="FlakyCo"),
    ]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "DeadCo" in resp.text
    assert "FlakyCo" in resp.text
    assert "2 of your 2" in resp.text


def test_results_flag_live_feed_with_junk_comment_as_label_warning(base_root: Path, client: TestClient) -> None:
    """A live feed whose inline comment is junk (would pollute jobs.company,
    #856) is surfaced as a distinct 'label looks off' warning — even though
    its URL resolves. It is NOT in the 'couldn't be verified' list."""
    fake = [
        _res("liveco", "live", http=200, company="LiveCo", ok=True),
        _res("junkco", "live", http=200, company="https://junk.com careers", ok=False),
    ]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "show as your company name" in resp.text.lower()
    assert "junk.com" in resp.text
    assert "couldn't be verified" not in resp.text.lower()


def test_results_clean_live_feeds_have_no_label_warning(base_root: Path, client: TestClient) -> None:
    fake = [_res("liveco", "live", http=200, company="LiveCo", ok=True)]
    with patch(_PROBE, return_value=fake):
        resp = client.get("/onboarding/feed-check/sess123/results")

    assert resp.status_code == 200
    assert "show as your company name" not in resp.text.lower()
