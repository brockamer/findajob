"""Tests for GET + POST /onboarding/spend-ceiling/{session_id}/ (#671).

Covers:
- GET renders the form with recommendation pre-filled
- POST with recommendation writes computed ceiling + redirects to /finish
- POST with override writes the numeric value + redirects to /finish
- POST with action=skip does NOT write the file + redirects to /finish
- /finish redirects to feed-config when active adapter unconfigured
- /finish redirects to gmail-config when no unconfigured adapter
- voice_redact_failed param propagates through /finish to next page
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app
from findajob.web.routes.settings_spend_ceiling import (
    _APPLIES_PER_WEEK_OPTIONS,
    _DEFAULT_APPLIES_PER_WEEK,
    _recommended_ceiling,
)

SID = "test-onboarding-sid"
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
"""


@pytest.fixture()
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "config").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.close()
    return tmp_path


@pytest.fixture()
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _ceiling_file(base_root: Path) -> Path:
    return base_root / "config" / "spend_ceiling.txt"


# ── GET /onboarding/spend-ceiling/{session_id}/ ───────────────────────────────


def test_get_renders_form(client: TestClient) -> None:
    """GET shows the ceiling form with applies_per_week select and skip button."""
    resp = client.get(f"/onboarding/spend-ceiling/{SID}/")
    assert resp.status_code == 200
    assert "applies_per_week" in resp.text
    assert "Skip for now" in resp.text
    # Recommendation for default applies is pre-filled
    expected = f"{_recommended_ceiling(_DEFAULT_APPLIES_PER_WEEK):.2f}"
    assert expected in resp.text


def test_get_renders_all_applies_options(client: TestClient) -> None:
    """GET shows all applies_per_week option values."""
    resp = client.get(f"/onboarding/spend-ceiling/{SID}/")
    assert resp.status_code == 200
    for n in _APPLIES_PER_WEEK_OPTIONS:
        assert str(n) in resp.text


# ── POST with recommendation ──────────────────────────────────────────────────


def test_post_recommendation_writes_file_and_redirects_to_finish(client: TestClient, base_root: Path) -> None:
    """POST with applies_per_week=3 + no override writes recommendation + redirects to /finish."""
    resp = client.post(
        f"/onboarding/spend-ceiling/{SID}/",
        data={"action": "save", "ceiling_override": "", "applies_per_week": "3"},
    )
    assert resp.status_code == 303
    assert f"/onboarding/spend-ceiling/{SID}/finish" in resp.headers["location"]

    f = _ceiling_file(base_root)
    assert f.exists()
    # Written with :.2f so compare against formatted value to avoid float repr drift.
    assert float(f.read_text().strip()) == pytest.approx(float(f"{_recommended_ceiling(3):.2f}"))


# ── POST with override ────────────────────────────────────────────────────────


def test_post_override_writes_file_and_redirects(client: TestClient, base_root: Path) -> None:
    """POST with ceiling_override=99.99 writes 99.99 to file + redirects."""
    resp = client.post(
        f"/onboarding/spend-ceiling/{SID}/",
        data={"action": "save", "ceiling_override": "99.99", "applies_per_week": "3"},
    )
    assert resp.status_code == 303
    assert f"/onboarding/spend-ceiling/{SID}/finish" in resp.headers["location"]

    f = _ceiling_file(base_root)
    assert f.exists()
    assert float(f.read_text().strip()) == pytest.approx(99.99)


# ── POST skip ────────────────────────────────────────────────────────────────


def test_post_skip_does_not_write_file_and_redirects(client: TestClient, base_root: Path) -> None:
    """action=skip does NOT write spend_ceiling.txt so dashboard banner appears."""
    resp = client.post(
        f"/onboarding/spend-ceiling/{SID}/",
        data={"action": "skip"},
    )
    assert resp.status_code == 303
    assert f"/onboarding/spend-ceiling/{SID}/finish" in resp.headers["location"]
    assert not _ceiling_file(base_root).exists()


# ── /finish redirect logic ────────────────────────────────────────────────────


def test_finish_redirects_to_gmail_when_no_active_sources_file(client: TestClient, base_root: Path) -> None:
    """/finish redirects to gmail-config when active_sources.txt absent (no feed-config gate)."""
    # No active_sources.txt — decide_post_interview_redirect returns False
    assert not (base_root / "config" / "active_sources.txt").exists()
    resp = client.get(f"/onboarding/spend-ceiling/{SID}/finish")
    assert resp.status_code == 303
    assert f"/onboarding/gmail-config/{SID}/" in resp.headers["location"]


def test_finish_redirects_to_feed_config_when_adapter_unconfigured(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/finish redirects to feed-config when active adapter has unconfigured env var."""

    # Write active_sources.txt with the real jobs-api14 adapter (env var will be absent)
    (base_root / "config" / "active_sources.txt").write_text("jobs-api14\n")

    # Remove the env var so jobs-api14 reports is_configured() = False
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    monkeypatch.delenv("JOBS_API14_KEY", raising=False)

    resp = client.get(f"/onboarding/spend-ceiling/{SID}/finish")
    assert resp.status_code == 303
    assert f"/onboarding/feed-config/{SID}" in resp.headers["location"]


# ── voice_redact_failed propagation ──────────────────────────────────────────


def test_voice_redact_failed_propagates_through_finish(client: TestClient, base_root: Path) -> None:
    """voice_redact_failed=1 on /finish is forwarded to the next page URL."""
    resp = client.get(f"/onboarding/spend-ceiling/{SID}/finish?voice_redact_failed=1")
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "voice_redact_failed=1" in location
