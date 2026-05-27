"""Tests for re-run interview prep button on the materials page (#875).

Coverage:

1. Button renders at stage='interview', not at other stages.
2. POST route triggers subprocess and redirects back.
3. POST route rejects non-interview stages with 409.
4. POST route respects per-job concurrency guard.
5. POST route respects spend-ceiling gate.
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

    def _make(*, stage: str = "interview") -> TestClient:
        companies = tmp_path / "companies"
        companies.mkdir(exist_ok=True)
        folder = companies / "Acme_Eng_2026-05-20_120000"
        folder.mkdir(exist_ok=True)
        (folder / "Tester Interview Prep - Acme - Sr Ops - 20260520-120000.md").write_text("# Notes")
        (folder / "JD - Acme - Sr Ops.txt").write_text("JD body.")

        db_path = tmp_path / "pipeline.db"
        if not db_path.exists():
            init_test_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
            "VALUES ('jid', 'fp', 'https://x.test', 'Sr Ops', 'Acme', 'test', ?, ?)",
            (stage, str(folder)),
        )
        conn.commit()
        conn.close()

        mark_complete(tmp_path)
        app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
        return TestClient(app)

    return _make


def test_button_renders_at_interview_stage(folder_client):
    client = folder_client(stage="interview")
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "rerun-interview-prep" in resp.text
    assert "Re-run interview prep" in resp.text


@pytest.mark.parametrize("stage", ["applied", "materials_drafted", "scored", "offer"])
def test_button_absent_at_non_interview_stages(stage, tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    companies = tmp_path / "companies"
    companies.mkdir(exist_ok=True)
    folder = companies / "Acme_Eng_2026-05-20_120000"
    folder.mkdir(exist_ok=True)
    (folder / "Tester Interview Prep - Acme - Sr Ops - 20260520-120000.md").write_text("# Notes")
    (folder / "JD - Acme - Sr Ops.txt").write_text("JD body.")

    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
        "VALUES ('jid', 'fp', 'https://x.test', 'Sr Ops', 'Acme', 'test', ?, ?)",
        (stage, str(folder)),
    )
    conn.commit()
    conn.close()

    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    client = TestClient(app)
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "rerun-interview-prep" not in resp.text


@patch("findajob.web.routes.board_actions._launch_interview_prep_subprocess")
def test_post_triggers_subprocess_and_redirects(mock_launch, folder_client):
    mock_launch.return_value = 42
    client = folder_client(stage="interview")
    resp = client.post("/materials/fp/rerun-interview-prep", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/materials/fp"
    mock_launch.assert_called_once()


def test_post_rejects_non_interview_stage(folder_client):
    client = folder_client(stage="applied")
    resp = client.post("/materials/fp/rerun-interview-prep")
    assert resp.status_code == 409


@patch("findajob.background_tasks.find_active_for_subject")
def test_post_concurrency_guard(mock_find_active, folder_client):
    mock_find_active.return_value = {"id": 1}
    client = folder_client(stage="interview")
    resp = client.post("/materials/fp/rerun-interview-prep", follow_redirects=False)
    assert resp.status_code == 303
    assert "interview_rerun_error=already_running" in resp.headers["location"]


@patch("findajob.spend_ceiling.check_launch_gate")
def test_post_spend_ceiling_gate(mock_gate, folder_client):
    from findajob.spend_ceiling import LaunchGateRefusal

    mock_gate.return_value = LaunchGateRefusal(current_sum_usd=50.0, ceiling_usd=50.0)
    client = folder_client(stage="interview")
    resp = client.post("/materials/fp/rerun-interview-prep", follow_redirects=False)
    assert resp.status_code == 303
    assert "interview_rerun_error=spend_ceiling" in resp.headers["location"]
