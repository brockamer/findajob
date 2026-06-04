"""Route tests for on-demand podcast generation (POST /materials/{fp}/podcast/{fmt}).

Covers the missing-artifacts path (#1029): when the briefing or interview-prep
markdown is not yet on disk — e.g. the operator clicks a podcast Generate button
while interview-prep is still generating those artifacts — the route must
redirect gracefully with ``?podcast_error=missing_artifacts`` rather than raise a
raw 409. The page is ``hx-boost``ed, so a raw 409 surfaces as a "Request failed"
toast and leaves the Alpine button stuck on "Generating…"; a 303 lets HTMX follow
the redirect, swap the page, and render a readable banner.

Per the test-real-codepath discipline (#610/#611): ``record_start`` is NOT mocked,
so the ``background_tasks.kind`` CHECK constraint is exercised by the happy path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture()
def folder_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def _make(
        *,
        stage: str = "interview",
        with_briefing: bool = True,
        with_interview_prep: bool = True,
    ) -> TestClient:
        companies = tmp_path / "companies"
        companies.mkdir(exist_ok=True)
        folder = companies / "Acme_Eng_2026-05-20_120000"
        folder.mkdir(exist_ok=True)
        (folder / "JD - Acme - Sr Ops.txt").write_text("JD body.")
        if with_briefing:
            (folder / "Tester Briefing - Acme - Sr Ops - 20260520-120000.md").write_text("# Briefing")
        if with_interview_prep:
            (folder / "Tester Interview Prep - Acme - Sr Ops - 20260520-120000.md").write_text("# Notes")

        db_path = tmp_path / "pipeline.db"
        if not db_path.exists():
            init_test_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path, raw_jd_text) "
            "VALUES ('jid', 'fp', 'https://x.test', 'Sr Ops', 'Acme', 'test', ?, ?, 'JD body.')",
            (stage, str(folder)),
        )
        conn.commit()
        conn.close()

        mark_complete(tmp_path)
        app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
        return TestClient(app)

    return _make


# ── Missing artifacts (#1029) ────────────────────────────────────────────


def test_podcast_post_missing_interview_prep_redirects(folder_client):
    """The reported bug: briefing present, interview-prep not yet written."""
    client = folder_client(with_briefing=True, with_interview_prep=False)
    resp = client.post("/materials/fp/podcast/brief", follow_redirects=False)
    assert resp.status_code == 303
    assert "podcast_error=missing_artifacts" in resp.headers["location"]


def test_podcast_post_missing_briefing_redirects(folder_client):
    client = folder_client(with_briefing=False, with_interview_prep=True)
    resp = client.post("/materials/fp/podcast/brief", follow_redirects=False)
    assert resp.status_code == 303
    assert "podcast_error=missing_artifacts" in resp.headers["location"]


# ── Happy path (route had no prior coverage) ─────────────────────────────


@patch("findajob.interview.orchestrator.generate_podcast_for_job")
def test_podcast_post_triggers_and_redirects(mock_gen, folder_client):
    client = folder_client(with_briefing=True, with_interview_prep=True)
    resp = client.post("/materials/fp/podcast/brief", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/materials/fp")
    assert "podcast_error" not in resp.headers["location"]
    mock_gen.assert_called_once()


# ── Banner render ────────────────────────────────────────────────────────


def test_podcast_missing_artifacts_banner_renders(folder_client, monkeypatch):
    """The 303 lands here; the folder page must render a readable banner
    (the podcast section requires GEMINI_API_KEY to be configured)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = folder_client()
    resp = client.get("/materials/fp?podcast_error=missing_artifacts")
    assert resp.status_code == 200
    assert "aren't ready yet" in resp.text
    assert "Request failed" not in resp.text
