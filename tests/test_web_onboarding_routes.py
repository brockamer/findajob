"""Integration tests for /onboarding/ routes (#148)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

_MINIMAL_SCHEMA = """
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
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()
    (tmp_path / "companies").mkdir()
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


def test_onboarding_index_returns_200(client: TestClient) -> None:
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text.lower()
    assert "step 1" in body
    assert "step 2" in body


def test_step_two_disabled_until_keys_collected(client: TestClient) -> None:
    """The Start interview button must be disabled when Step 1 has not run."""
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    # The fieldset wrapping the Start button is disabled when keys_collected=False
    assert 'disabled aria-disabled="true"' in body
    assert "Save your API keys above before continuing." in body


def test_rerun_mode_shows_backup_warning(client: TestClient) -> None:
    resp = client.get("/onboarding/?mode=rerun")
    assert resp.status_code == 200
    assert ".backups/" in resp.text
    assert "/config/" in resp.text  # pointer to editor for partial updates


def test_first_run_hides_backup_warning(client: TestClient) -> None:
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    assert "Existing config will be backed up" not in resp.text


def test_paste_back_routes_are_gone(client: TestClient) -> None:
    """The paste-back path was removed 2026-05-02. Both endpoints must 404."""
    assert client.get("/onboarding/prompt").status_code == 404
    assert client.post("/onboarding/inject", data={"emission": ""}).status_code == 404


def test_tools_page_links_to_onboarding_rerun(client: TestClient) -> None:
    resp = client.get("/tools/")
    assert resp.status_code == 200
    body = resp.text
    assert "/onboarding/?mode=rerun" in body
    assert "Run onboarding interview" in body
