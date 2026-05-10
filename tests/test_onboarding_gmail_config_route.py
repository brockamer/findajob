"""Tests for /onboarding/gmail-config/{session_id}/* (#407).

The gmail-config gate sits between feed-config (optional, upstream) and the
connections gate (#571, terminal, downstream). /finish preserves the #407
invariant that an IMAP test must have passed before the gate releases; on
success it hands off to the connections gate which writes the sentinel.
The sentinel is no longer written from this route.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import gmail_imap
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
    return tmp_path


@pytest.fixture
def client(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Route gmail_imap config/state writes into the test base_root so we don't
    # poison whatever's installed at the repo's real config/ path.
    monkeypatch.setattr(gmail_imap, "GMAIL_CONFIG_PATH", str(base_root / "config" / "gmail.json"))
    monkeypatch.setattr(gmail_imap, "GMAIL_STATE_PATH", str(base_root / "config" / "gmail_state.json"))
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


SID = "test-session-id"


def test_get_renders_onboarding_wrapper_around_existing_card(client: TestClient) -> None:
    """The gate page extends base.html, includes the existing _card.html, and
    surfaces the onboarding-specific Skip + Continue buttons."""
    response = client.get(f"/onboarding/gmail-config/{SID}/")
    assert response.status_code == 200
    body = response.text
    # Onboarding wrapper copy
    assert "Set up Gmail job-alert ingestion" in body
    # 2FA prereq guidance is surfaced
    assert "myaccount.google.com" in body
    # Existing _card.html is included (form posts to /config/gmail/save)
    assert "/config/gmail/save" in body
    # Skip + Continue buttons target the gate's POST endpoints
    assert f"/onboarding/gmail-config/{SID}/skip" in body
    assert f"/onboarding/gmail-config/{SID}/finish" in body


def test_post_skip_redirects_to_connections_gate_without_writing_sentinel(client: TestClient, base_root: Path) -> None:
    """Skip is always allowed — it hands off to the connections gate, which
    is responsible for writing the sentinel. Skipping here must not write the
    sentinel itself."""
    assert not (base_root / "data" / ".onboarding-complete").exists()
    response = client.post(f"/onboarding/gmail-config/{SID}/skip")
    assert response.status_code == 303
    assert response.headers["location"] == f"/onboarding/connections/{SID}/"
    assert not (base_root / "data" / ".onboarding-complete").exists()


def test_post_finish_blocked_when_no_config(client: TestClient, base_root: Path) -> None:
    """Without a saved gmail.json, /finish must NOT write the sentinel.  The
    user gets a 400 with a pointer to either save+test or skip."""
    response = client.post(f"/onboarding/gmail-config/{SID}/finish")
    assert response.status_code == 400
    assert "Save your Gmail credentials" in response.text
    assert not (base_root / "data" / ".onboarding-complete").exists()


def test_post_finish_blocked_when_test_not_run(client: TestClient, base_root: Path) -> None:
    """Saved config without a successful test run must be rejected."""
    cfg = gmail_imap.GmailConfig(
        address="someone@example.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-05-04T00:00:00Z",
    )
    gmail_imap.save_config(cfg)
    # No test run yet → state.last_login_at is None
    response = client.post(f"/onboarding/gmail-config/{SID}/finish")
    assert response.status_code == 400
    assert "Run Test connection successfully" in response.text
    assert not (base_root / "data" / ".onboarding-complete").exists()


def test_post_finish_blocked_when_test_failed(client: TestClient, base_root: Path) -> None:
    """Saved config + last_error=auth_failed must be rejected."""
    cfg = gmail_imap.GmailConfig(
        address="someone@example.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-05-04T00:00:00Z",
    )
    gmail_imap.save_config(cfg)
    state = gmail_imap.load_state()
    gmail_imap.save_state(replace(state, last_error="auth_failed"))

    response = client.post(f"/onboarding/gmail-config/{SID}/finish")
    assert response.status_code == 400
    assert "Run Test connection successfully" in response.text
    assert not (base_root / "data" / ".onboarding-complete").exists()


def test_post_finish_advances_to_connections_gate_when_test_passed(client: TestClient, base_root: Path) -> None:
    """Saved config + state.last_login_at set + no error → /finish redirects
    to the connections gate. The sentinel is not written here — that
    responsibility moved to the connections gate (#571)."""
    cfg = gmail_imap.GmailConfig(
        address="someone@example.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-05-04T00:00:00Z",
    )
    gmail_imap.save_config(cfg)
    state = gmail_imap.load_state()
    gmail_imap.save_state(replace(state, last_login_at="2026-05-04T00:01:00Z", last_error=None))

    response = client.post(f"/onboarding/gmail-config/{SID}/finish")
    assert response.status_code == 303
    assert response.headers["location"] == f"/onboarding/connections/{SID}/"
    assert not (base_root / "data" / ".onboarding-complete").exists()
