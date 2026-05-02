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
    (tmp_path / "config" / "rapidapi_feeds.yaml").write_text(
        Path("config/rapidapi_feeds.yaml.example").read_text()
    )
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
