"""#1098 — the folder view warns when a tailored resume holds text from a
master-resume section marked "not for submitted resumes"."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web.app import create_app

MASTER = (
    "# Jane Candidate\n\n"
    "## Experience\n"
    "- Led the night shift through a site move without missing a delivery date.\n\n"
    "## Selected Quotes\n"
    "*(For interview prep, cover letters, and outreach — not for submitted resumes)*\n"
    '- "She turned a struggling night shift into the steadiest team in the building."\n'
    '- "Keeps <b>calm</b> during every single overnight cutover window."\n\n'
    "## Skills\n"
    "- Scheduling, inventory control\n"
)
RESUME_NAME = "Candidate Resume - Acme - Shift Lead - 20261021-120000.md"
COPIED = "## Summary\nShift lead who turned a struggling night shift into the steadiest team.\n"
CLEAN = "## Summary\nShift lead with steady delivery across site moves.\n"
WARNING = "Remove marked text before you send this resume"


@pytest.fixture()
def folder_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def _make(*, files: dict[str, str], master: str | None = MASTER) -> TestClient:
        if master is not None:
            (tmp_path / "candidate_context").mkdir(exist_ok=True)
            (tmp_path / "candidate_context" / "master_resume.md").write_text(master)
        companies = tmp_path / "companies"
        folder = companies / "Acme_Shift_Lead_2026-10-21_120000"
        folder.mkdir(parents=True)
        for name, body in files.items():
            (folder / name).write_text(body)

        from findajob.db.migrate import apply_pending

        db_path = tmp_path / "pipeline.db"
        conn = sqlite3.connect(db_path)
        apply_pending(conn)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
            "VALUES ('jid', 'fp', 'https://x.test', 'Shift Lead', 'Acme', 'test', 'materials_drafted', ?)",
            (str(folder),),
        )
        conn.commit()
        conn.close()

        mark_complete(tmp_path)
        app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
        return TestClient(app)

    return _make


def test_warning_lists_copied_marked_text(folder_client):
    resp = folder_client(files={RESUME_NAME: COPIED}).get("/materials/fp")

    assert resp.status_code == 200
    assert WARNING in resp.text
    assert RESUME_NAME in resp.text
    assert "steadiest team in the building" in resp.text


def test_no_warning_for_clean_resume(folder_client):
    resp = folder_client(files={RESUME_NAME: CLEAN}).get("/materials/fp")

    assert resp.status_code == 200
    assert WARNING not in resp.text


def test_no_warning_when_master_resume_missing(folder_client):
    resp = folder_client(files={RESUME_NAME: COPIED}, master=None).get("/materials/fp")

    assert resp.status_code == 200
    assert WARNING not in resp.text


def test_resume_changes_file_is_not_checked(folder_client):
    files = {
        RESUME_NAME: CLEAN,
        "Candidate Resume Changes - Acme - Shift Lead - 20261021-120000.md": COPIED,
    }
    resp = folder_client(files=files).get("/materials/fp")

    assert resp.status_code == 200
    assert WARNING not in resp.text


def test_applied_snapshot_is_not_checked(folder_client):
    files = {
        RESUME_NAME: CLEAN,
        "Candidate Resume - Acme - Shift Lead - 20261021-120000.applied-2026-10-21.md": COPIED,
    }
    resp = folder_client(files=files).get("/materials/fp")

    assert resp.status_code == 200
    assert WARNING not in resp.text


def test_warning_escapes_marked_text(folder_client):
    resume = "## Summary\nKeeps <b>calm</b> during every single overnight cutover window.\n"
    resp = folder_client(files={RESUME_NAME: resume}).get("/materials/fp")

    assert WARNING in resp.text
    assert "&lt;b&gt;calm&lt;/b&gt;" in resp.text
    assert "<b>calm</b>" not in resp.text
