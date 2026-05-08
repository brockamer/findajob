"""Routes tests for speculative ingest. FastAPI TestClient + in-memory DB."""

from __future__ import annotations

import json as _json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from findajob.web.routes import speculative as spec_routes


def _make_app(db_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(spec_routes.router)
    spec_routes.DB_PATH = db_path
    return app


def _make_db(tmp_path: Path) -> Path:
    """Build a tmp pipeline.db via the production migration runner.

    Pre-M5/M6 this fixture maintained a hand-written subset of the schema
    that drifted whenever a column landed (#339 cumulative_cost_usd,
    M6 background_tasks). Using apply_pending eliminates the drift surface.
    """
    from findajob.db.migrate import apply_pending

    db = tmp_path / "p.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()
    return db


# Approve path now uses the same fixture — apply_pending creates jobs +
# audit_log alongside speculative_requests, so the previous _make_db_with_jobs
# split is unnecessary.
_make_db_with_jobs = _make_db


# ── T21: POST /ingest/speculative ────────────────────────────────────────


def test_post_speculative_inserts_row_and_spawns_subprocess(tmp_path):
    db = _make_db(tmp_path)
    app = _make_app(db)
    client = TestClient(app)

    with patch("findajob.web.routes.speculative.subprocess.Popen") as mock_popen:
        # The launcher reads ``proc.pid`` to backfill background_tasks.pid;
        # the SQLite UPDATE binds it as INTEGER, so a MagicMock pid trips
        # ProgrammingError. A real int keeps the launcher happy.
        mock_popen.return_value.pid = 99999
        resp = client.post(
            "/ingest/speculative",
            data={"company": "PSIQuantum", "hint": "advanced computing", "personal_notes": ""},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/speculative/status/")
    assert mock_popen.call_count == 1
    args = mock_popen.call_args[0][0]
    assert any("run_speculative_research.py" in str(a) for a in args)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM speculative_requests").fetchone()
    assert row is not None
    assert row["company"] == "PSIQuantum"
    assert row["status"] == "researching"


def test_post_speculative_rejects_empty_company(tmp_path):
    db = _make_db(tmp_path)
    app = _make_app(db)
    client = TestClient(app)
    resp = client.post(
        "/ingest/speculative",
        data={"company": "", "hint": "", "personal_notes": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 400


# ── T22: status page + poll fragment ─────────────────────────────────────


def test_get_status_renders_researching(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO speculative_requests (company, status) VALUES ('PSI', 'researching')")
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    resp = client.get("/speculative/status/1")
    assert resp.status_code == 200
    assert "Researching" in resp.text


def test_poll_returns_fragment(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO speculative_requests (company, status, error_message) VALUES ('PSI', 'failed', 'budget exceeded')"
    )
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    resp = client.get("/speculative/status/1/poll")
    assert resp.status_code == 200
    assert "Research failed" in resp.text
    assert "budget exceeded" in resp.text
    # The retry/trash forms on the failed-status fragment must opt out of
    # hx-boost so the 303 redirect navigates the browser (#319).
    assert 'hx-boost="false"' in resp.text


def test_researching_fragment_preserves_hx_trigger_across_swaps(tmp_path):
    """The fragment is swapped via outerHTML, so the rendered branch element
    itself must carry id="status" + hx-* attrs. Without this, the first swap
    drops the trigger and polling silently stops after one tick (#485)."""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO speculative_requests (company, status) VALUES ('PSI', 'researching')")
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    # The poll endpoint returns just the fragment (what htmx swaps in).
    resp = client.get("/speculative/status/1/poll")
    assert resp.status_code == 200
    assert 'id="status"' in resp.text
    assert 'hx-trigger="every 5s"' in resp.text
    assert 'hx-get="/speculative/status/1/poll"' in resp.text
    assert 'hx-swap="outerHTML"' in resp.text


def test_status_page_renders_hx_trigger_inline(tmp_path):
    """The full status page must also render the hx-trigger directly so the
    initial page load arms the poll without depending on a wrapper. #485."""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO speculative_requests (company, status) VALUES ('PSI', 'researching')")
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    resp = client.get("/speculative/status/1")
    assert resp.status_code == 200
    assert 'hx-trigger="every 5s"' in resp.text


# ── T23: review page ─────────────────────────────────────────────────────


def test_get_review_renders_briefing_and_cards(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO speculative_requests (company, status, briefing_md, role_cards_json)
           VALUES ('PSI', 'ready_for_review', '# Briefing\nbody', ?)""",
        (
            _json.dumps(
                [
                    {
                        "title": "Critical Infra Eng",
                        "description": "Own GPU cluster bring-up.",
                        "why_this_fits_candidate": "Resume bullet match.",
                        "likely_team_or_org": "SiteOps",
                        "suggested_contact_type": "hiring_manager",
                    }
                ]
            ),
        ),
    )
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    resp = client.get("/speculative/review/1")
    assert resp.status_code == 200
    assert "Critical Infra Eng" in resp.text
    assert "Own GPU cluster bring-up" in resp.text
    assert "SiteOps" in resp.text
    # All cards default-checked (keep on by default)
    assert 'value="0" checked' in resp.text
    # The form must opt out of hx-boost so the 303 redirect from approve/regenerate/trash
    # is followed by the browser, not swallowed by HTMX (#319).
    assert 'hx-boost="false"' in resp.text


def test_get_review_redirects_when_not_ready(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO speculative_requests (company, status) VALUES ('PSI', 'researching')")
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    resp = client.get("/speculative/review/1", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/speculative/status/1"


# ── T24: approve / regenerate / trash ────────────────────────────────────


def test_approve_writes_jobs_and_redirects_to_board(tmp_path):
    db = _make_db_with_jobs(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO speculative_requests (company, status, briefing_md, role_cards_json, briefing_folder)
           VALUES ('PSI', 'ready_for_review', '# b', ?, 'PSI_SPECULATIVE_2026-04-28_140000')""",
        (
            _json.dumps(
                [
                    {
                        "title": "Eng A",
                        "description": "D",
                        "why_this_fits_candidate": "W",
                        "likely_team_or_org": "T",
                        "suggested_contact_type": "recruiter",
                    },
                    {
                        "title": "Eng B",
                        "description": "D",
                        "why_this_fits_candidate": "W",
                        "likely_team_or_org": "T",
                        "suggested_contact_type": "recruiter",
                    },
                ]
            ),
        ),
    )
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    resp = client.post("/speculative/approve/1", data={"keep": ["1"]}, follow_redirects=False)
    assert resp.status_code == 303
    # /board/ is not a registered route; canonical landing is /board/dashboard
    # (#485). Without this, the 303 → GET /board/ 404s with the FastAPI
    # default JSON detail.
    assert resp.headers["location"] == "/board/dashboard"

    conn = sqlite3.connect(str(db))
    titles = [r[0] for r in conn.execute("SELECT title FROM jobs").fetchall()]
    assert titles == ["[SPEC] Eng B"]


def test_trash_marks_status_and_redirects_to_ingest(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO speculative_requests (company, status) VALUES ('PSI', 'ready_for_review')")
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    resp = client.post("/speculative/trash/1", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ingest/"
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT status FROM speculative_requests").fetchone()[0] == "trashed"


def test_regenerate_409_when_research_already_in_flight(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO speculative_requests (company, status) VALUES ('PSI', 'researching')")
    conn.commit()
    conn.close()

    app = _make_app(db)
    client = TestClient(app)
    with patch("findajob.web.routes.speculative.subprocess.Popen") as mock_popen:
        resp = client.post("/speculative/regenerate/1", follow_redirects=False)
    assert resp.status_code == 409
    assert mock_popen.call_count == 0
