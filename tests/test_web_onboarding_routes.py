"""Integration tests for /onboarding/ routes (#148)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

# Schema is built from the production migration runner so the fixture
# matches the real shape exactly. Pre-M5 these fixtures carried hand-written
# CREATE TABLE blocks that drifted from production whenever a column landed.


def _build_pipeline_db(db_path: Path) -> None:
    """Run the migration runner against a fresh DB at ``db_path``.

    Equivalent to what ``ops/entrypoint.sh`` does at every container start.
    """
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    _build_pipeline_db(db_path)
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
    assert "Run a full onboarding interview" in body


@pytest.fixture()
def client_with_credentials_only_session(tmp_path: Path) -> TestClient:
    """Client whose DB holds a credentials-only session (history=[]) but no
    chat turns.  This is the exact post-Step-1 state that triggered the
    resume-banner false positive (#401 PR B Task 1).
    """
    db_path = tmp_path / "pipeline.db"
    _build_pipeline_db(db_path)
    # Insert a credentials-only session row WITH its tester credential
    # already set. Under the M5 migration runner, all schema columns
    # exist by the time create_app runs, so we can write the row in
    # one shot rather than the prior two-phase pattern (insert row,
    # then UPDATE the column post-create_app).
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO onboarding_sessions
               (id, history_json, captured_blocks_json, started_at, last_turn_at,
                tester_openrouter_key)
           VALUES ('cred-only-session', '[]', '{}',
                   datetime('now'), datetime('now'),
                   'sk-or-v1-fake-tester-key-for-test')"""
    )
    conn.commit()
    conn.close()
    (tmp_path / "companies").mkdir()
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


def test_credentials_only_session_does_not_trigger_resume_banner(
    client_with_credentials_only_session: TestClient,
) -> None:
    """A credentials-only session (history=[]) must NOT show the resume banner.

    Bug: _active_session_for_index returned the credentials-only row created by
    POST /onboarding/keys because find_active matched it (no completed_at, recent
    last_turn_at).  The fix adds a post-find_active guard: if history is empty,
    treat it as no active session.
    """
    resp = client_with_credentials_only_session.get("/onboarding/")
    assert resp.status_code == 200
    assert 'id="resume-banner"' not in resp.text
