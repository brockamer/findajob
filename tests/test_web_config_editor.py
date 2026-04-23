"""Integration tests for the /config/ editor web routes (#149)."""

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
def base_root(tmp_path: Path) -> Path:
    """Populate a realistic subset of the allowlist on disk."""
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "candidate_context" / "profile.md").write_text("# Profile\nHello.\n")
    # master_resume.md intentionally omitted — tests the missing-file case.

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "jsearch_queries.txt").write_text("site reliability engineer\n")
    (tmp_path / "config" / "feed_urls.txt").write_text("acme\nexample-corp\n")

    (tmp_path / "config" / "roles").mkdir()
    (tmp_path / "config" / "roles" / "job_scorer.md").write_text("# Scorer role\n")
    (tmp_path / "config" / "roles" / "cover_letter_writer.md").write_text("# CL role\n")

    return tmp_path


@pytest.fixture()
def client(base_root: Path, tmp_path: Path) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()

    app = create_app(
        companies_root=companies,
        db_path=db_path,
        base_root=base_root,
    )
    return TestClient(app)


def test_index_lists_files_by_category(client: TestClient) -> None:
    resp = client.get("/config/")
    assert resp.status_code == 200
    html = resp.text
    assert "Candidate context" in html
    assert "Search config" in html
    assert "Role prompts" in html
    assert "candidate_context/profile.md" in html
    assert "candidate_context/master_resume.md" in html
    assert "config/jsearch_queries.txt" in html
    assert "config/roles/job_scorer.md" in html
    assert "config/roles/cover_letter_writer.md" in html
    assert 'href="/config/files/candidate_context/profile.md"' in html
    assert "missing" in html.lower() or "not yet" in html.lower()
