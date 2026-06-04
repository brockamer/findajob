"""Tests for on-demand study-guide / flashcard buttons on the materials page (#873).

Mirrors ``test_materials_interview_rerun.py``. Coverage:

1. Buttons render at stage='interview', not at other stages.
2. POST routes trigger the generator and redirect back.
3. POST routes reject non-interview stages with 409.
4. POST routes respect the per-job concurrency guard.
5. POST routes respect the spend-ceiling gate.
6. POST routes redirect with ?study_materials_error=missing_artifacts when
   required prep artifacts are missing (#1029).

Per the test-real-codepath discipline (#610/#611): the generator function is
mocked, but ``record_start`` is NOT — so the ``background_tasks.kind`` CHECK
constraint is exercised by every passing test. A missing migration would fail
the INSERT, not silently pass.
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
        if with_interview_prep:
            (folder / "Tester Interview Prep - Acme - Sr Ops - 20260520-120000.md").write_text("# Notes")
        (folder / "JD - Acme - Sr Ops.txt").write_text("JD body.")
        if with_briefing:
            (folder / "Tester Briefing - Acme - Sr Ops - 20260520-120000.md").write_text("# Briefing")

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


# ── Render gating ────────────────────────────────────────────────────────


def test_buttons_render_at_interview_stage(folder_client):
    client = folder_client(stage="interview")
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "/study-guide" in resp.text
    assert "/flashcards" in resp.text
    assert "Generate Study Guide" in resp.text
    assert "Generate Flashcards" in resp.text


@pytest.mark.parametrize("stage", ["applied", "materials_drafted", "scored", "offer"])
def test_buttons_absent_at_non_interview_stages(stage, folder_client):
    client = folder_client(stage=stage)
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Generate Study Guide" not in resp.text
    assert "Generate Flashcards" not in resp.text


def test_existing_artifact_renders_regenerate_label(tmp_path, monkeypatch):
    """When a study-guide artifact already exists, the button reads
    'Regenerate' (exists=True Jinja branch) — the runtime-only path the
    mock-heavy route tests skip."""
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    companies = tmp_path / "companies"
    companies.mkdir()
    folder = companies / "Acme_Eng_2026-05-20_120000"
    folder.mkdir()
    (folder / "Tester Interview Prep - Acme - Sr Ops - 20260520-120000.md").write_text("# Notes")
    (folder / "Tester Briefing - Acme - Sr Ops - 20260520-120000.md").write_text("# Briefing")
    # Pre-existing study guide → exists=True for that artifact only.
    (folder / "Tester Study Guide - Acme - Sr Ops - 20260520-120000.md").write_text("# SG")

    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
        "VALUES ('jid', 'fp', 'https://x.test', 'Sr Ops', 'Acme', 'test', 'interview', ?)",
        (str(folder),),
    )
    conn.commit()
    conn.close()

    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Regenerate Study Guide" in resp.text  # exists=True branch
    assert "Generate Flashcards" in resp.text  # exists=False branch (no flashcard file)


# ── POST triggers + redirects ────────────────────────────────────────────


@patch("findajob.interview.orchestrator.generate_study_guide_for_job")
def test_study_guide_post_triggers_and_redirects(mock_gen, folder_client):
    client = folder_client(stage="interview")
    resp = client.post("/materials/fp/study-guide", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/materials/fp")
    mock_gen.assert_called_once()


@patch("findajob.interview.orchestrator.generate_flashcards_for_job")
def test_flashcards_post_triggers_and_redirects(mock_gen, folder_client):
    client = folder_client(stage="interview")
    resp = client.post("/materials/fp/flashcards", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/materials/fp")
    mock_gen.assert_called_once()


# ── Stage gate ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ["study-guide", "flashcards"])
def test_post_rejects_non_interview_stage(route, folder_client):
    client = folder_client(stage="applied")
    resp = client.post(f"/materials/fp/{route}")
    assert resp.status_code == 409


# ── Concurrency guard ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("route", "kind"),
    [("study-guide", "study_guide"), ("flashcards", "flashcards")],
)
@patch("findajob.background_tasks.find_active_for_subject")
def test_post_concurrency_guard(mock_find_active, route, kind, folder_client):
    mock_find_active.return_value = {"id": 1}
    client = folder_client(stage="interview")
    resp = client.post(f"/materials/fp/{route}", follow_redirects=False)
    assert resp.status_code == 303
    assert "study_materials_error=already_generating" in resp.headers["location"]


# ── Spend ceiling ────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ["study-guide", "flashcards"])
@patch("findajob.spend_ceiling.check_launch_gate")
def test_post_spend_ceiling_gate(mock_gate, route, folder_client):
    from findajob.spend_ceiling import LaunchGateRefusal

    mock_gate.return_value = LaunchGateRefusal(current_sum_usd=50.0, ceiling_usd=50.0)
    client = folder_client(stage="interview")
    resp = client.post(f"/materials/fp/{route}", follow_redirects=False)
    assert resp.status_code == 303
    assert "study_materials_error=spend_ceiling" in resp.headers["location"]


# ── Missing artifacts ────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ["study-guide", "flashcards"])
@pytest.mark.parametrize("missing", ["briefing", "interview_prep"])
def test_post_missing_artifacts_redirects(route, missing, folder_client):
    """#1029: a missing briefing/interview-prep artifact redirects gracefully
    with ?study_materials_error=missing_artifacts rather than raising a raw 409
    (which hx-boost would surface as a "Request failed" toast + stuck button)."""
    client = folder_client(
        stage="interview",
        with_briefing=(missing != "briefing"),
        with_interview_prep=(missing != "interview_prep"),
    )
    resp = client.post(f"/materials/fp/{route}", follow_redirects=False)
    assert resp.status_code == 303
    assert "study_materials_error=missing_artifacts" in resp.headers["location"]


def test_study_missing_artifacts_banner_renders(folder_client):
    """The 303 lands here; the folder page must render a readable banner."""
    client = folder_client(stage="interview")
    resp = client.get("/materials/fp?study_materials_error=missing_artifacts")
    assert resp.status_code == 200
    assert "aren't ready yet" in resp.text
    assert "Request failed" not in resp.text
