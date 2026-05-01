"""Integration tests for the NUX guard dependency (#148)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app

_MINIMAL_SCHEMA = """
CREATE TABLE jobs (
    id TEXT,
    fingerprint TEXT,
    title TEXT,
    company TEXT,
    stage TEXT,
    relevance_score INTEGER,
    fit_score REAL,
    probability_score REAL,
    interview_likelihood INTEGER,
    location TEXT,
    remote_status TEXT,
    known_contacts TEXT,
    comp_estimate TEXT,
    ai_notes TEXT,
    created_at TEXT,
    stage_updated TEXT,
    url TEXT,
    prep_folder_path TEXT,
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
def unconfigured_client(tmp_path: Path) -> TestClient:
    """Stack with no sentinel = not yet onboarded."""
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


@pytest.fixture()
def configured_client(tmp_path: Path) -> TestClient:
    """Stack with sentinel written = onboarded."""
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()
    (tmp_path / "companies").mkdir()
    mark_complete(tmp_path)
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


# ---- Gated routes redirect when unconfigured ----


@pytest.mark.parametrize("path", ["/", "/board/dashboard", "/materials/", "/stats/funnel"])
def test_gated_routes_redirect_without_sentinel(unconfigured_client: TestClient, path: str) -> None:
    """`/` joined the gated set in #339 Task 9 — a fresh stack drops the
    visitor straight into onboarding instead of the marketing landing page."""
    resp = unconfigured_client.get(path)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/onboarding/"


# ---- Gated routes pass through when configured ----


@pytest.mark.parametrize("path", ["/", "/board/dashboard", "/stats/funnel"])
def test_gated_routes_pass_with_sentinel(configured_client: TestClient, path: str) -> None:
    resp = configured_client.get(path)
    # 200 or a different redirect — anything NOT a 307 to /onboarding/
    assert not (resp.status_code == 307 and resp.headers.get("location") == "/onboarding/")


# ---- Ungated routes are always reachable ----


@pytest.mark.parametrize("path", ["/healthz", "/config/", "/tools/", "/ingest/"])
def test_ungated_routes_reachable_without_sentinel(unconfigured_client: TestClient, path: str) -> None:
    resp = unconfigured_client.get(path)
    assert not (resp.status_code == 307 and resp.headers.get("location") == "/onboarding/")
