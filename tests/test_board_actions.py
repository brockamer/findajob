"""Tests for web POST handlers in src/findajob/web/routes/board_actions.py.

Each handler is exercised against a real TestClient-backed FastAPI app and an
on-disk SQLite DB so the audit_log JOIN behavior matches production. The
subprocess.Popen call that dispatches prep is monkeypatched on the
board_actions module so tests don't actually fork prep_application.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import utils
from findajob.web import routes as _web_routes
from findajob.web.app import create_app

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    remote_status TEXT DEFAULT 'Unknown',
    known_contacts TEXT DEFAULT '',
    comp_estimate TEXT DEFAULT '',
    ai_notes TEXT,
    relevance_score INTEGER,
    interview_likelihood INTEGER,
    stage TEXT,
    stage_updated TEXT,
    apply_flag INTEGER DEFAULT 0,
    prep_folder_path TEXT,
    reject_reason TEXT DEFAULT '',
    user_notes TEXT DEFAULT '',
    gdrive_folder_url TEXT,
    source TEXT DEFAULT 'test',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now')),
    changed_by TEXT DEFAULT 'system'
);
"""


def _insert_job(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    stage: str,
    job_id: str | None = None,
    company: str = "Acme Corp",
    title: str = "Senior Ops",
    url: str = "https://example.com/job",
    score: int = 8,
) -> str:
    job_id = job_id or fingerprint.replace("fp", "id")
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, stage, relevance_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, fingerprint, url, title, company, stage, score),
    )
    conn.commit()
    return job_id


@pytest.fixture()
def popen_calls(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args, **_kw):
            calls.append(args)

    from findajob.web.routes import board_actions

    monkeypatch.setattr(board_actions.subprocess, "Popen", _FakePopen)
    return calls


@pytest.fixture()
def client(tmp_path: Path, monkeypatch, popen_calls) -> TestClient:
    monkeypatch.setattr(utils, "LOG_PATH", str(tmp_path / "events.jsonl"))

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _insert_job(conn, fingerprint="fp_scored", stage="scored")
    _insert_job(conn, fingerprint="fp_manual", stage="manual_review")
    _insert_job(conn, fingerprint="fp_prep", stage="prep_in_progress")
    _insert_job(conn, fingerprint="fp_drafted", stage="materials_drafted")
    _insert_job(conn, fingerprint="fp_applied", stage="applied")
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    app = create_app(companies_root=companies, db_path=db_path)
    client = TestClient(app)
    client._db_path = db_path  # expose for assertions
    return client


def _fetch_stage(client: TestClient, fingerprint: str) -> str | None:
    conn = sqlite3.connect(client._db_path)
    row = conn.execute("SELECT stage FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
    conn.close()
    return row[0] if row else None


def _fetch_audit(client: TestClient, fingerprint: str) -> list[tuple[str, str | None, str | None]]:
    conn = sqlite3.connect(client._db_path)
    rows = conn.execute(
        "SELECT al.field_changed, al.old_value, al.new_value "
        "FROM audit_log al JOIN jobs j ON j.id = al.job_id "
        "WHERE j.fingerprint=? ORDER BY al.id",
        (fingerprint,),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


# ── /prep handler ──────────────────────────────────────────────────────────


class TestPrep:
    def test_happy_path_flags_scored_job(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_scored/prep")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_scored") == "prep_in_progress"

        audit = _fetch_audit(client, "fp_scored")
        assert any(a == ("stage", "scored", "prep_in_progress") for a in audit)

        assert len(popen_calls) == 1
        args = popen_calls[0]
        assert "prep_application.py" in args[1]
        assert args[-1] == "--no-sync"

    def test_happy_path_flags_manual_review_job(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_manual/prep")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_manual") == "prep_in_progress"
        assert len(popen_calls) == 1

    def test_returns_updated_row_html(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_scored/prep")

        assert response.status_code == 200
        # HTMX swaps a <tr> — the response should be a table row, not the full page
        assert response.text.strip().startswith("<tr")
        assert 'data-fingerprint="fp_scored"' in response.text
        # After the transition the row shows the Prep in progress indicator
        assert "Prep in progress" in response.text

    def test_404_on_unknown_fingerprint(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_nonexistent/prep")

        assert response.status_code == 404
        assert popen_calls == []

    def test_idempotent_on_prep_in_progress(self, client: TestClient, popen_calls):
        """Double-click during prep: second POST is a no-op, returns current row."""
        response = client.post("/board/jobs/fp_prep/prep")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_prep") == "prep_in_progress"
        # No second subprocess launched, no audit row written
        assert popen_calls == []
        assert _fetch_audit(client, "fp_prep") == []

    def test_idempotent_on_materials_drafted(self, client: TestClient, popen_calls):
        """Clicking Flag for Prep on an already-drafted job is a no-op."""
        response = client.post("/board/jobs/fp_drafted/prep")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_drafted") == "materials_drafted"
        assert popen_calls == []

    def test_double_post_launches_prep_once(self, client: TestClient, popen_calls):
        """Two rapid POSTs: first dispatches, second hits the idempotency guard."""
        first = client.post("/board/jobs/fp_scored/prep")
        second = client.post("/board/jobs/fp_scored/prep")

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(popen_calls) == 1
        assert _fetch_stage(client, "fp_scored") == "prep_in_progress"


def test_router_registered_on_app(client: TestClient):
    """The new board_actions router must be included in the aggregated router."""
    # Smoke-check the aggregated router has the new path registered.
    paths = {route.path for route in _web_routes.router.routes}
    assert "/board/jobs/{fingerprint}/prep" in paths
