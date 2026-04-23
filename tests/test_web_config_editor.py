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


def test_editor_shows_existing_content(client: TestClient) -> None:
    resp = client.get("/config/files/candidate_context/profile.md")
    assert resp.status_code == 200
    html = resp.text
    assert "<textarea" in html
    assert "# Profile\nHello." in html
    assert 'hx-post="/config/files/candidate_context/profile.md"' in html
    assert "candidate_context/profile.md" in html


def test_editor_shows_empty_textarea_for_missing_file(client: TestClient) -> None:
    resp = client.get("/config/files/candidate_context/master_resume.md")
    assert resp.status_code == 200
    html = resp.text
    assert "<textarea" in html
    assert "does not exist" in html.lower() or "will be created" in html.lower()


def test_editor_rejects_unlisted_file(client: TestClient) -> None:
    resp = client.get("/config/files/data/pipeline.db")
    assert resp.status_code == 403


def test_editor_rejects_path_traversal(client: TestClient) -> None:
    resp = client.get("/config/files/config/../../etc/passwd")
    assert resp.status_code in (403, 404)


def test_editor_rejects_absolute_path_segment(client: TestClient) -> None:
    # FastAPI's `{path:path}` strips a leading slash off the arg, so the
    # effective relpath is "etc/passwd" — still not in allowlist → 403.
    resp = client.get("/config/files//etc/passwd")
    assert resp.status_code in (403, 404)


def test_save_writes_content_to_disk(client: TestClient, base_root: Path) -> None:
    new_content = "# Profile\nUpdated from the editor.\n"
    resp = client.post(
        "/config/files/candidate_context/profile.md",
        data={"content": new_content},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    on_disk = (base_root / "candidate_context" / "profile.md").read_text(encoding="utf-8")
    assert on_disk == new_content


def test_save_creates_missing_file(client: TestClient, base_root: Path) -> None:
    target = base_root / "candidate_context" / "master_resume.md"
    assert not target.exists()
    resp = client.post(
        "/config/files/candidate_context/master_resume.md",
        data={"content": "# Master resume\n"},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    assert target.read_text(encoding="utf-8") == "# Master resume\n"


def test_save_preserves_utf8_and_newlines(client: TestClient, base_root: Path) -> None:
    content = "Line 1\nLine 2\n— em-dash — α β γ\n"
    resp = client.post(
        "/config/files/config/jsearch_queries.txt",
        data={"content": content},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    on_disk = (base_root / "config" / "jsearch_queries.txt").read_bytes()
    assert on_disk.decode("utf-8") == content
    assert b"\r\n" not in on_disk


def test_save_rejects_unlisted_file(client: TestClient) -> None:
    resp = client.post(
        "/config/files/data/pipeline.db",
        data={"content": "anything"},
    )
    assert resp.status_code == 403


def test_save_rejects_traversal(client: TestClient) -> None:
    resp = client.post(
        "/config/files/config/../../etc/passwd",
        data={"content": "oops"},
    )
    assert resp.status_code in (403, 404)


def test_save_result_partial_has_expected_attrs(client: TestClient) -> None:
    resp = client.post(
        "/config/files/candidate_context/profile.md",
        data={"content": "# Profile\n"},
    )
    assert resp.status_code == 200
    assert 'data-outcome="success"' in resp.text
    assert "Saved" in resp.text


def test_tools_page_links_to_config(client: TestClient) -> None:
    resp = client.get("/tools/")
    assert resp.status_code == 200
    html = resp.text
    assert 'href="/config/"' in html
    assert "Edit config files" in html or "Config editor" in html
