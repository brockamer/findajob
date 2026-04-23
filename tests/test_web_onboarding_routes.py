"""Integration tests for /onboarding/ routes (#148)."""

from __future__ import annotations

import shutil
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
    created_at TEXT DEFAULT (datetime('now'))
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
    # Copy the real onboarding_interviewer role into the tmpdir so
    # /onboarding/prompt can read it. GET /onboarding/prompt is a
    # real filesystem read of {base_root}/config/roles/onboarding_interviewer.md.
    (tmp_path / "config" / "roles").mkdir(parents=True)
    repo_role = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"
    shutil.copy(repo_role, tmp_path / "config" / "roles" / "onboarding_interviewer.md")

    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


def test_onboarding_index_returns_200(client: TestClient) -> None:
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    assert "onboarding" in body.lower()
    assert 'name="emission"' in body  # paste textarea
    assert "copy the prompt" in body.lower() or "Copy the prompt" in body


def test_rerun_mode_shows_backup_warning(client: TestClient) -> None:
    resp = client.get("/onboarding/?mode=rerun")
    assert resp.status_code == 200
    assert ".backups/" in resp.text
    assert "/config/" in resp.text  # pointer to editor for partial updates


def test_first_run_hides_backup_warning(client: TestClient) -> None:
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    assert "Existing config will be backed up" not in resp.text


def test_onboarding_prompt_endpoint_returns_role_text(client: TestClient) -> None:
    resp = client.get("/onboarding/prompt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # The interview role begins with this heading line
    assert "Onboarding Interviewer v2" in resp.text
