"""Tests for GET /onboarding/feed-config/{session_id} (#408)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    # write a minimal curation file so the route can read it
    (tmp_path / "config" / "rapidapi_feeds.yaml").write_text(Path("config/rapidapi_feeds.yaml.example").read_text())
    # active source = jsearch (the candidate just picked it)
    (tmp_path / "config" / "active_sources.txt").write_text("jsearch\n")
    return tmp_path


@pytest.fixture
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def test_get_renders_form_with_adapter_specific_walkthrough(client: TestClient) -> None:
    response = client.get("/onboarding/feed-config/test-session-id")
    assert response.status_code == 200
    body = response.text
    assert "JSearch" in body
    assert "rapidapi.com" in body
    assert "API key" in body or "Key" in body  # form label


def test_get_404_when_no_active_sources_pending(base_root: Path, client: TestClient) -> None:
    """If there's no active_sources.txt, there's no feed to config."""
    (base_root / "config" / "active_sources.txt").unlink()
    response = client.get("/onboarding/feed-config/test-session-id")
    assert response.status_code == 404


def test_post_skip_writes_sentinel_no_key_change(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/onboarding/feed-config/test-session-id",
        data={"skip": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "skip" in response.text.lower() or "configure later" in response.text.lower()
    # Sentinel was NOT written here — only on /finish (which is the next click)


def test_post_runs_live_test_and_writes_key_on_success(
    client: TestClient,
    base_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful POST writes the key into data/.env and renders the success card."""
    monkeypatch.setattr("findajob.paths.BASE", str(base_root))
    (base_root / "data" / ".env").write_text("OTHER=x\n")
    (base_root / "config" / "jsearch_queries.txt").write_text("nurse\nteacher\n")

    # Patch live_test to return a synthetic success result
    from findajob.fetchers.adapters.base import LiveTestResult, QueryResult

    fake_result = LiveTestResult(
        ok=True,
        bucket="success",
        per_query=[
            QueryResult(query="nurse", count=12),
            QueryResult(query="teacher", count=8),
        ],
        auth_error=None,
    )
    monkeypatch.setattr(
        "findajob.fetchers.adapters.jsearch.JSearchAdapter.live_test",
        lambda self, queries: fake_result,
    )

    response = client.post(
        "/onboarding/feed-config/test-session-id",
        data={"api_key": "test-key-50-chars"},
    )
    assert response.status_code == 200
    body = response.text
    assert "12" in body  # nurse count
    assert "8" in body  # teacher count
    assert "JSEARCH_API_KEY=test-key-50-chars" in (base_root / "data" / ".env").read_text()


def test_post_auth_failure_does_not_write_key(
    client: TestClient,
    base_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("findajob.paths.BASE", str(base_root))
    (base_root / "data" / ".env").write_text("OTHER=x\n")
    (base_root / "config" / "jsearch_queries.txt").write_text("nurse\n")

    from findajob.fetchers.adapters.base import LiveTestResult

    fake_result = LiveTestResult(
        ok=False,
        bucket="auth",
        per_query=[],
        auth_error="HTTP 401",
    )
    monkeypatch.setattr(
        "findajob.fetchers.adapters.jsearch.JSearchAdapter.live_test",
        lambda self, queries: fake_result,
    )

    response = client.post(
        "/onboarding/feed-config/test-session-id",
        data={"api_key": "bad-key"},
    )
    assert response.status_code == 200
    body = response.text
    assert "couldn't connect" in body.lower() or "didn't recognize" in body.lower()
    # Key was NOT written
    assert "JSEARCH_API_KEY" not in (base_root / "data" / ".env").read_text()


def test_post_finish_redirects_to_gmail_config_gate(client: TestClient, base_root: Path) -> None:
    """Per #407, feed-config /finish hands off to the Gmail-config gate
    instead of writing the sentinel itself. Sentinel must NOT be written
    here — the connections gate (#571), downstream of gmail-config, is the
    single sentinel-write site."""
    response = client.post("/onboarding/feed-config/test-session-id/finish")
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/gmail-config/test-session-id/"
    assert not (base_root / "data" / ".onboarding-complete").exists()
