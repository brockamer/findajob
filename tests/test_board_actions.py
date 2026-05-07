"""Tests for web POST handlers in src/findajob/web/routes/board_actions.py.

Each handler is exercised against a real TestClient-backed FastAPI app and an
on-disk SQLite DB so the audit_log JOIN behavior matches production. The
subprocess.Popen call that dispatches prep is monkeypatched on the
board_actions module so tests don't actually fork prep_application.py.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import utils
from findajob.onboarding import mark_complete
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
    fit_score REAL,
    probability_score REAL,
    interview_likelihood INTEGER,
    score_status TEXT,
    score_flag_reason TEXT,
    stage TEXT,
    stage_updated TEXT,
    apply_flag INTEGER DEFAULT 0,
    prep_folder_path TEXT,
    raw_jd_text TEXT,
    reject_reason TEXT DEFAULT '',
    user_notes TEXT DEFAULT '',
    gdrive_folder_url TEXT,
    source TEXT DEFAULT 'test',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    relevance_score INTEGER,
    reject_reason TEXT NOT NULL,
    jd_excerpt TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    operation TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER,
    success INTEGER DEFAULT 1,
    error_message TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    logged_at TEXT DEFAULT (datetime('now'))
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
        "INSERT INTO jobs (id, fingerprint, url, title, company, stage, relevance_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, fingerprint, url, title, company, stage, score),
    )
    conn.commit()
    return job_id


@pytest.fixture()
def popen_calls(monkeypatch) -> list[list[str]]:
    """Capture subprocess.Popen invocations from both the web layer and
    findajob.actions (notify_waitlist_resurface launches notify.py via Popen)."""
    calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, args, **_kw):
            calls.append(args)

    from findajob import actions
    from findajob.web.routes import board_actions

    monkeypatch.setattr(board_actions.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(actions.subprocess, "Popen", _FakePopen)
    return calls


@pytest.fixture()
def client(tmp_path: Path, monkeypatch, popen_calls) -> TestClient:
    from findajob import actions
    from findajob.web.routes import board_actions

    monkeypatch.setattr(utils, "LOG_PATH", str(tmp_path / "events.jsonl"))
    # /apply resolves its destination folder via board_actions.BASE; actions.BASE
    # drives handle_waitlist / handle_reactivate folder moves. Point both at the
    # test's tmp_path so folder ops don't reach into the real repo.
    monkeypatch.setattr(board_actions, "BASE", str(tmp_path))
    monkeypatch.setattr(actions, "BASE", str(tmp_path))

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _insert_job(conn, fingerprint="fp_scored", stage="scored")
    _insert_job(conn, fingerprint="fp_manual", stage="manual_review")
    _insert_job(conn, fingerprint="fp_prep", stage="prep_in_progress")
    _insert_job(conn, fingerprint="fp_drafted", stage="materials_drafted")
    _insert_job(conn, fingerprint="fp_applied", stage="applied")
    _insert_job(conn, fingerprint="fp_interview", stage="interview")
    _insert_job(conn, fingerprint="fp_offer", stage="offer")
    # Different company so waitlist-resurface tests don't see fp_applied as a sibling
    _insert_job(conn, fingerprint="fp_waitlisted", stage="waitlisted", company="Waitlist Co")
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    client = TestClient(app)
    client._db_path = db_path  # expose for assertions
    client._tmp_path = tmp_path
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
        assert "--no-sync" not in args

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
        # After the transition the row shows the Prep indicator badge
        assert 'title="Prep subprocess running"' in response.text

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
    for endpoint in (
        "apply",
        "interview",
        "offer",
        "withdraw",
        "waitlist",
        "reactivate",
        "promote",
        "un-reject",
        "reject",
        "not-selected",
        "regenerate",
        "notes",
    ):
        assert f"/board/jobs/{{fingerprint}}/{endpoint}" in paths


def _fetch_feedback(client: TestClient, fingerprint: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(client._db_path)
    rows = conn.execute(
        "SELECT fb.reject_reason, fb.title FROM feedback_log fb JOIN jobs j ON j.id = fb.job_id WHERE j.fingerprint=?",
        (fingerprint,),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


# ── /reject handler ───────────────────────────────────────────────────────


class TestReject:
    def _seed_prep_folder(self, client: TestClient, fingerprint: str, parent: str = "") -> Path:
        parent_path = client._tmp_path / "companies" / parent if parent else client._tmp_path / "companies"
        parent_path.mkdir(parents=True, exist_ok=True)
        folder = parent_path / f"Acme_Ops_reject_{fingerprint}"
        folder.mkdir()
        (folder / "resume.pdf").touch()
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE fingerprint=?", (str(folder), fingerprint))
        conn.commit()
        conn.close()
        return folder

    def test_happy_path_from_dashboard(self, client: TestClient):
        folder = self._seed_prep_folder(client, "fp_drafted")

        response = client.post("/board/jobs/fp_drafted/reject", data={"reason": "Low Fit Score"})

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_drafted") == "rejected"

        # feedback_log written with the reason + title
        fb = _fetch_feedback(client, "fp_drafted")
        assert fb == [("Low Fit Score", "Senior Ops")]

        # Folder moved to _rejected/ with a REJECTED_ marker
        conn = sqlite3.connect(client._db_path)
        new_path = conn.execute("SELECT prep_folder_path FROM jobs WHERE fingerprint='fp_drafted'").fetchone()[0]
        conn.close()
        assert "_rejected" in new_path
        assert not folder.exists()
        markers = [f for f in os.listdir(new_path) if f.startswith("REJECTED_")]
        assert len(markers) == 1
        assert "Low_Fit_Score" in markers[0]

    def test_happy_path_without_folder(self, client: TestClient):
        response = client.post("/board/jobs/fp_scored/reject", data={"reason": "Wrong Level"})

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_scored") == "rejected"
        fb = _fetch_feedback(client, "fp_scored")
        assert fb == [("Wrong Level", "Senior Ops")]

    def test_empty_reason_defaults_to_other(self, client: TestClient):
        response = client.post("/board/jobs/fp_scored/reject", data={"reason": ""})

        assert response.status_code == 200
        fb = _fetch_feedback(client, "fp_scored")
        assert fb == [("Other", "Senior Ops")]

    def test_fires_waitlist_resurface(self, client: TestClient, popen_calls):
        """A waitlisted sibling at the same company triggers a notification."""
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, stage) "
            "VALUES ('sib','fp_sib','u','Other','Acme Corp','waitlisted')"
        )
        conn.commit()
        conn.close()

        client.post("/board/jobs/fp_scored/reject", data={"reason": "Low Fit Score"})

        notify_calls = [c for c in popen_calls if any("notify.py" in arg for arg in c)]
        assert len(notify_calls) == 1

    def test_idempotent_on_already_rejected(self, client: TestClient):
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET stage='rejected' WHERE fingerprint='fp_scored'")
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_scored/reject", data={"reason": "Other"})

        assert response.status_code == 200
        assert _fetch_feedback(client, "fp_scored") == []

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/reject", data={"reason": "Other"})
        assert response.status_code == 404


# ── /not-selected handler ────────────────────────────────────────────────


class TestNotSelected:
    def _seed_applied_folder(self, client: TestClient, fingerprint: str) -> Path:
        applied_dir = client._tmp_path / "companies" / "_applied"
        applied_dir.mkdir(parents=True, exist_ok=True)
        folder = applied_dir / f"Acme_Ops_notsel_{fingerprint}"
        folder.mkdir()
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE fingerprint=?", (str(folder), fingerprint))
        conn.commit()
        conn.close()
        return folder

    def test_happy_path_from_applied(self, client: TestClient):
        folder = self._seed_applied_folder(client, "fp_applied")

        response = client.post(
            "/board/jobs/fp_applied/not-selected",
            data={"reason": "Too Senior"},
        )

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_applied") == "not_selected"

        # Folder stays in _applied/ with a NOT_SELECTED_ marker
        assert folder.exists()
        markers = [f for f in os.listdir(folder) if f.startswith("NOT_SELECTED_")]
        assert len(markers) == 1
        assert "Too_Senior" in markers[0]

    def test_does_not_write_feedback_log(self, client: TestClient):
        """Company rejections must not contaminate the scorer feedback loop."""
        self._seed_applied_folder(client, "fp_applied")
        client.post("/board/jobs/fp_applied/not-selected", data={"reason": "Too Senior"})

        assert _fetch_feedback(client, "fp_applied") == []

    def test_happy_path_from_interview(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_interview/not-selected",
            data={"reason": "Skills Mismatch"},
        )

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_interview") == "not_selected"

    def test_happy_path_from_offer(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_offer/not-selected",
            data={"reason": "Company Not a Fit"},
        )

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_offer") == "not_selected"

    def test_409_on_pre_application_stage(self, client: TestClient):
        """Not Selected only valid for applied/interview/offer."""
        response = client.post(
            "/board/jobs/fp_scored/not-selected",
            data={"reason": "Too Senior"},
        )

        assert response.status_code == 409
        assert _fetch_stage(client, "fp_scored") == "scored"

    def test_409_on_waitlisted(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_waitlisted/not-selected",
            data={"reason": "Too Senior"},
        )
        assert response.status_code == 409

    def test_empty_reason_defaults_to_company_passed(self, client: TestClient):
        self._seed_applied_folder(client, "fp_applied")
        client.post("/board/jobs/fp_applied/not-selected", data={"reason": ""})

        conn = sqlite3.connect(client._db_path)
        reject_reason = conn.execute("SELECT reject_reason FROM jobs WHERE fingerprint='fp_applied'").fetchone()[0]
        conn.close()
        assert reject_reason == "Company passed"

    def test_fires_waitlist_resurface(self, client: TestClient, popen_calls):
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, stage) "
            "VALUES ('sib','fp_sib','u','Other','Acme Corp','waitlisted')"
        )
        conn.commit()
        conn.close()

        client.post(
            "/board/jobs/fp_applied/not-selected",
            data={"reason": "Too Senior"},
        )

        notify_calls = [c for c in popen_calls if any("notify.py" in arg for arg in c)]
        assert len(notify_calls) == 1

    def test_idempotent_on_already_not_selected(self, client: TestClient):
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET stage='not_selected' WHERE fingerprint='fp_applied'")
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_applied/not-selected", data={"reason": "Other"})

        assert response.status_code == 200
        assert _fetch_feedback(client, "fp_applied") == []

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_nonexistent/not-selected",
            data={"reason": "Other"},
        )
        assert response.status_code == 404


# ── /regenerate handler ───────────────────────────────────────────────────


class TestRegenerate:
    def _seed_prep_folder(self, client: TestClient, fingerprint: str) -> Path:
        folder = client._tmp_path / "companies" / f"Acme_regen_{fingerprint}"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").touch()
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "UPDATE jobs SET prep_folder_path=?, gdrive_folder_url='https://drive/abc' WHERE fingerprint=?",
            (str(folder), fingerprint),
        )
        conn.commit()
        conn.close()
        return folder

    def test_happy_path_deletes_folder_and_dispatches(self, client: TestClient, popen_calls):
        folder = self._seed_prep_folder(client, "fp_drafted")

        response = client.post("/board/jobs/fp_drafted/regenerate")

        assert response.status_code == 200
        assert not folder.exists()

        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT stage, prep_folder_path, gdrive_folder_url, apply_flag FROM jobs WHERE fingerprint='fp_drafted'"
        ).fetchone()
        conn.close()
        assert row[0] == "prep_in_progress"
        assert row[1] is None
        assert row[2] is None
        assert row[3] == 1

        assert len(popen_calls) == 1
        assert "prep_application.py" in popen_calls[0][1]

    def test_no_op_on_prep_in_progress(self, client: TestClient, popen_calls):
        """Double-click during regen: second POST returns current row, no new subprocess."""
        response = client.post("/board/jobs/fp_prep/regenerate")

        assert response.status_code == 200
        assert response.text.strip().startswith("<tr")
        assert _fetch_stage(client, "fp_prep") == "prep_in_progress"
        assert popen_calls == []
        assert _fetch_audit(client, "fp_prep") == []

    def test_without_folder_still_dispatches(self, client: TestClient, popen_calls):
        """Regenerate is valid even if no prep_folder_path is recorded."""
        response = client.post("/board/jobs/fp_drafted/regenerate")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_drafted") == "prep_in_progress"
        assert len(popen_calls) == 1

    def test_404_on_unknown_fingerprint(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_nonexistent/regenerate")
        assert response.status_code == 404
        assert popen_calls == []


# ── Concurrency cap ──────────────────────────────────────────────────────


class TestPrepConcurrencyCap:
    def _set_three_in_flight(self, client: TestClient) -> None:
        """Three jobs already in prep_in_progress (fp_prep + 2 new)."""
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, stage) "
            "VALUES ('inflight1','fp_inflight1','u','T','C','prep_in_progress')"
        )
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, stage) "
            "VALUES ('inflight2','fp_inflight2','u','T','C','prep_in_progress')"
        )
        # fp_prep is already prep_in_progress → 3 in flight total
        conn.commit()
        conn.close()

    def test_prep_returns_429_when_cap_reached(self, client: TestClient, popen_calls):
        self._set_three_in_flight(client)

        response = client.post("/board/jobs/fp_scored/prep")

        assert response.status_code == 429
        assert "queue full" in response.text.lower()
        # DB unchanged
        assert _fetch_stage(client, "fp_scored") == "scored"
        assert _fetch_audit(client, "fp_scored") == []
        # No new subprocess
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []

    def test_regenerate_returns_429_when_cap_reached(self, client: TestClient, popen_calls):
        self._set_three_in_flight(client)

        # fp_drafted is at materials_drafted — regen would push to 4 in flight
        response = client.post("/board/jobs/fp_drafted/regenerate")

        assert response.status_code == 429
        # Stage unchanged
        assert _fetch_stage(client, "fp_drafted") == "materials_drafted"
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []

    def test_prep_allowed_at_cap_minus_one(self, client: TestClient, popen_calls):
        """With 2 in flight, a 3rd prep is allowed."""
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, stage) "
            "VALUES ('inflight1','fp_inflight1','u','T','C','prep_in_progress')"
        )
        # fp_prep is the 2nd in flight
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_scored/prep")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_scored") == "prep_in_progress"
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert len(prep_calls) == 1

    def test_idempotent_prep_bypasses_cap(self, client: TestClient, popen_calls):
        """A re-click on an in-flight or drafted job returns the row even if cap is reached."""
        self._set_three_in_flight(client)

        # fp_drafted is at materials_drafted, so /prep returns idempotent row before cap check
        response = client.post("/board/jobs/fp_drafted/prep")

        assert response.status_code == 200
        assert response.text.strip().startswith("<tr")
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []


# ── /notes handler ───────────────────────────────────────────────────────


def _fetch_user_notes(client: TestClient, fingerprint: str) -> str | None:
    conn = sqlite3.connect(client._db_path)
    row = conn.execute("SELECT user_notes FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
    conn.close()
    return row[0] if row else None


class TestNotes:
    def test_happy_path_saves_note(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "Follow up in two weeks"},
        )

        assert response.status_code == 200
        assert _fetch_user_notes(client, "fp_applied") == "Follow up in two weeks"

    def test_response_is_rerendered_td_with_input(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "Recruiter call Wed"},
        )
        # Response is a <td> containing the input with the saved value
        assert response.text.strip().startswith("<td")
        assert 'value="Recruiter call Wed"' in response.text
        assert 'name="notes"' in response.text
        assert 'hx-post="/board/jobs/fp_applied/notes"' in response.text

    def test_empty_note_clears_db_column(self, client: TestClient):
        # First set some text
        client.post("/board/jobs/fp_applied/notes", data={"notes": "original"})
        assert _fetch_user_notes(client, "fp_applied") == "original"

        # Then clear it
        response = client.post("/board/jobs/fp_applied/notes", data={"notes": ""})

        assert response.status_code == 200
        assert _fetch_user_notes(client, "fp_applied") == ""

    def test_no_audit_log_entry(self, client: TestClient):
        """Notes are free-text, rewritten on every keystroke debounce — no audit noise."""
        client.post("/board/jobs/fp_applied/notes", data={"notes": "anything"})
        assert _fetch_audit(client, "fp_applied") == []

    def test_does_not_affect_stage(self, client: TestClient):
        client.post("/board/jobs/fp_applied/notes", data={"notes": "leave stage alone"})
        assert _fetch_stage(client, "fp_applied") == "applied"

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_nonexistent/notes",
            data={"notes": "anything"},
        )
        assert response.status_code == 404


# ── /waitlist handler ─────────────────────────────────────────────────────


class TestWaitlist:
    def test_happy_path_from_dashboard_stage(self, client: TestClient):
        response = client.post("/board/jobs/fp_drafted/waitlist")

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_drafted") == "waitlisted"

        audit = _fetch_audit(client, "fp_drafted")
        assert any(a == ("stage", "materials_drafted", "waitlisted") for a in audit)

    def test_happy_path_moves_folder(self, client: TestClient):
        folder = client._tmp_path / "companies" / "Acme_Ops_waitlist_test"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").touch()
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE fingerprint='fp_drafted'", (str(folder),))
        conn.commit()
        conn.close()

        client.post("/board/jobs/fp_drafted/waitlist")

        assert not folder.exists()
        conn = sqlite3.connect(client._db_path)
        new_path = conn.execute("SELECT prep_folder_path FROM jobs WHERE fingerprint='fp_drafted'").fetchone()[0]
        conn.close()
        assert "_waitlisted" in new_path
        assert Path(new_path).is_dir()

    def test_idempotent_on_already_waitlisted(self, client: TestClient):
        response = client.post("/board/jobs/fp_waitlisted/waitlist")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_waitlisted") == "waitlisted"
        assert _fetch_audit(client, "fp_waitlisted") == []

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/waitlist")
        assert response.status_code == 404


# ── /reactivate handler ───────────────────────────────────────────────────


class TestReactivate:
    def test_happy_path_without_folder(self, client: TestClient):
        response = client.post("/board/jobs/fp_waitlisted/reactivate")

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_waitlisted") == "scored"

        audit = _fetch_audit(client, "fp_waitlisted")
        assert any(a == ("stage", "waitlisted", "scored") for a in audit)

    def test_happy_path_restores_folder(self, client: TestClient):
        """With a folder in _waitlisted/, reactivate moves it back and sets stage=materials_drafted."""
        folder = client._tmp_path / "companies" / "_waitlisted" / "Acme_Ops_reactivate"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").touch()
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "UPDATE jobs SET prep_folder_path=? WHERE fingerprint='fp_waitlisted'",
            (str(folder),),
        )
        conn.commit()
        conn.close()

        client.post("/board/jobs/fp_waitlisted/reactivate")

        assert _fetch_stage(client, "fp_waitlisted") == "materials_drafted"
        conn = sqlite3.connect(client._db_path)
        new_path = conn.execute("SELECT prep_folder_path FROM jobs WHERE fingerprint='fp_waitlisted'").fetchone()[0]
        conn.close()
        assert "_waitlisted" not in new_path
        assert Path(new_path).is_dir()
        assert not folder.exists()

    def test_409_on_non_waitlisted_job(self, client: TestClient):
        response = client.post("/board/jobs/fp_scored/reactivate")

        assert response.status_code == 409
        # Stage unchanged
        assert _fetch_stage(client, "fp_scored") == "scored"

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/reactivate")
        assert response.status_code == 404


# ── /promote handler ──────────────────────────────────────────────────────


class TestPromote:
    def test_happy_path_from_manual_review(self, client: TestClient):
        response = client.post("/board/jobs/fp_manual/promote")

        assert response.status_code == 200
        assert response.text == ""

        conn = sqlite3.connect(client._db_path)
        row = conn.execute("SELECT stage, relevance_score FROM jobs WHERE fingerprint='fp_manual'").fetchone()
        conn.close()
        assert row[0] == "scored"
        assert row[1] == 7

        audit = _fetch_audit(client, "fp_manual")
        assert any(a == ("stage", "manual_review", "scored") for a in audit)

    def test_happy_path_from_archive_scored(self, client: TestClient):
        """Archive-tab Promote on a score-6 stage='scored' row bumps to score=7."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_archive_6", stage="scored", score=6)
        conn.close()

        response = client.post("/board/jobs/fp_archive_6/promote")

        assert response.status_code == 200
        assert response.text == ""

        conn = sqlite3.connect(client._db_path)
        row = conn.execute("SELECT stage, relevance_score FROM jobs WHERE fingerprint='fp_archive_6'").fetchone()
        conn.close()
        assert row[0] == "scored"
        assert row[1] == 7

    def test_409_on_post_application_stage(self, client: TestClient):
        response = client.post("/board/jobs/fp_applied/promote")

        assert response.status_code == 409
        assert _fetch_stage(client, "fp_applied") == "applied"

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/promote")
        assert response.status_code == 404


# ── /un-reject handler ────────────────────────────────────────────────────


def _seed_user_rejected_job(
    client: TestClient,
    fingerprint: str,
    *,
    with_folder: bool = False,
    with_feedback: bool = True,
) -> Path | None:
    """Seed a stage='rejected' row with the side effects a real user rejection
    would leave behind: a feedback_log row and (optionally) a folder under
    companies/_rejected/."""
    conn = sqlite3.connect(client._db_path)
    _insert_job(conn, fingerprint=fingerprint, stage="rejected", score=4)
    job_id = conn.execute("SELECT id FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
    folder_path: Path | None = None
    if with_folder:
        folder_path = client._tmp_path / "companies" / "_rejected" / f"Acme_Ops_{fingerprint}"
        folder_path.mkdir(parents=True)
        (folder_path / "resume.pdf").touch()
        conn.execute(
            "UPDATE jobs SET prep_folder_path=?, reject_reason='Wrong domain' WHERE id=?",
            (str(folder_path), job_id),
        )
    else:
        conn.execute("UPDATE jobs SET reject_reason='Wrong domain' WHERE id=?", (job_id,))
    if with_feedback:
        conn.execute(
            "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason, jd_excerpt) "
            "VALUES (?, 'Senior Ops', 'Acme Corp', 4, 'Wrong domain', '')",
            (job_id,),
        )
    conn.commit()
    conn.close()
    return folder_path


class TestUnReject:
    def test_happy_path_without_folder(self, client: TestClient):
        _seed_user_rejected_job(client, "fp_user_rej")

        response = client.post("/board/jobs/fp_user_rej/un-reject")

        assert response.status_code == 200
        assert response.text == ""

        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT stage, relevance_score, reject_reason FROM jobs WHERE fingerprint='fp_user_rej'"
        ).fetchone()
        conn.close()
        assert row[0] == "scored"
        assert row[1] == 8
        assert row[2] == ""

        # feedback_log row removed so the scorer's feedback loop stays clean
        assert _fetch_feedback(client, "fp_user_rej") == []

        audit = _fetch_audit(client, "fp_user_rej")
        assert any(a == ("stage", "rejected", "scored") for a in audit)

    def test_happy_path_restores_folder(self, client: TestClient):
        """Folder under companies/_rejected/ is moved back to companies/."""
        rejected_folder = _seed_user_rejected_job(client, "fp_user_rej_f", with_folder=True)
        assert rejected_folder is not None

        client.post("/board/jobs/fp_user_rej_f/un-reject")

        conn = sqlite3.connect(client._db_path)
        new_path = conn.execute("SELECT prep_folder_path FROM jobs WHERE fingerprint='fp_user_rej_f'").fetchone()[0]
        conn.close()
        assert "_rejected" not in new_path
        assert Path(new_path).is_dir()
        assert not rejected_folder.exists()

    def test_409_on_company_not_selected(self, client: TestClient):
        """stage='not_selected' (company rejection) cannot be un-rejected — only user rejection."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_not_sel", stage="not_selected")
        conn.close()

        response = client.post("/board/jobs/fp_not_sel/un-reject")

        assert response.status_code == 409
        assert _fetch_stage(client, "fp_not_sel") == "not_selected"

    def test_409_on_scored_stage(self, client: TestClient):
        """A scored row uses /promote, not /un-reject — gate on stage='rejected' only."""
        response = client.post("/board/jobs/fp_scored/un-reject")

        assert response.status_code == 409
        assert _fetch_stage(client, "fp_scored") == "scored"

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/un-reject")
        assert response.status_code == 404


# ── /apply handler ─────────────────────────────────────────────────────────


class TestApply:
    def _seed_prep_folder(self, client: TestClient, fingerprint: str) -> Path:
        folder = client._tmp_path / "companies" / f"Acme_Ops_{fingerprint}"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").touch()
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE fingerprint=?", (str(folder), fingerprint))
        conn.commit()
        conn.close()
        return folder

    def test_happy_path_from_materials_drafted(self, client: TestClient):
        folder = self._seed_prep_folder(client, "fp_drafted")

        response = client.post("/board/jobs/fp_drafted/apply")

        assert response.status_code == 200
        assert response.text == ""  # empty body = HTMX removes the row
        assert _fetch_stage(client, "fp_drafted") == "applied"
        # Folder moved into _applied/
        conn = sqlite3.connect(client._db_path)
        new_path = conn.execute("SELECT prep_folder_path FROM jobs WHERE fingerprint='fp_drafted'").fetchone()[0]
        conn.close()
        assert "_applied" in new_path
        assert not folder.exists()
        assert Path(new_path).is_dir()

        audit = _fetch_audit(client, "fp_drafted")
        assert any(a == ("stage", "materials_drafted", "applied") for a in audit)

    def test_happy_path_without_folder(self, client: TestClient):
        """Apply without a prep folder still transitions DB state."""
        response = client.post("/board/jobs/fp_drafted/apply")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_drafted") == "applied"

    def test_idempotent_on_already_applied(self, client: TestClient):
        response = client.post("/board/jobs/fp_applied/apply")

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_applied") == "applied"
        # No audit written — idempotency short-circuit
        assert _fetch_audit(client, "fp_applied") == []

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/apply")
        assert response.status_code == 404


# ── /interview handler ────────────────────────────────────────────────────


class TestInterview:
    def test_happy_path_from_applied(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_applied/interview")

        assert response.status_code == 200
        assert response.text.strip().startswith("<tr")
        assert 'data-fingerprint="fp_applied"' in response.text
        assert _fetch_stage(client, "fp_applied") == "interview"

        audit = _fetch_audit(client, "fp_applied")
        assert any(a == ("stage", "applied", "interview") for a in audit)

        # Interview transition launches interview_prep generator (#258).
        assert len(popen_calls) == 1
        args = popen_calls[0]
        assert "interview_prep.py" in args[1]
        # Subprocess receives company, title, job_id (no URL — JD comes from DB).
        assert args[2:] == ["Acme Corp", "Senior Ops", "id_applied"]

    def test_reclick_regenerates(self, client: TestClient, popen_calls):
        """Re-clicking 'Interviewing' on an already-interview job re-launches
        interview_prep so the operator can refresh after a recruiter sends
        panel info. No audit row written for the no-op stage transition."""
        response = client.post("/board/jobs/fp_interview/interview")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_interview") == "interview"
        assert _fetch_audit(client, "fp_interview") == []
        assert len(popen_calls) == 1
        assert "interview_prep.py" in popen_calls[0][1]

    def test_404_on_unknown_fingerprint(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_nonexistent/interview")
        assert response.status_code == 404
        assert popen_calls == []


# ── /offer handler ────────────────────────────────────────────────────────


class TestOffer:
    def test_happy_path_from_interview(self, client: TestClient):
        response = client.post("/board/jobs/fp_interview/offer")

        assert response.status_code == 200
        assert response.text.strip().startswith("<tr")
        assert _fetch_stage(client, "fp_interview") == "offer"

        audit = _fetch_audit(client, "fp_interview")
        assert any(a == ("stage", "interview", "offer") for a in audit)

    def test_happy_path_from_applied(self, client: TestClient):
        """Recruiter-straight-to-offer flow."""
        response = client.post("/board/jobs/fp_applied/offer")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_applied") == "offer"

    def test_idempotent_on_already_offer(self, client: TestClient):
        response = client.post("/board/jobs/fp_offer/offer")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_offer") == "offer"
        assert _fetch_audit(client, "fp_offer") == []

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/offer")
        assert response.status_code == 404


# ── /withdraw handler ─────────────────────────────────────────────────────


class TestWithdraw:
    def _seed_waitlisted_sibling(self, client: TestClient, company: str) -> None:
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, stage) "
            "VALUES ('sib', 'fp_sibling', 'u', 'Other role', ?, 'waitlisted')",
            (company,),
        )
        conn.commit()
        conn.close()

    def test_happy_path_empties_response(self, client: TestClient):
        response = client.post("/board/jobs/fp_applied/withdraw")

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_applied") == "withdrawn"

        audit = _fetch_audit(client, "fp_applied")
        assert any(a == ("stage", "applied", "withdrawn") for a in audit)

    def test_fires_waitlist_resurface_notification(self, client: TestClient, popen_calls):
        """A waitlisted sibling at the same company triggers a notification."""
        self._seed_waitlisted_sibling(client, "Acme Corp")

        client.post("/board/jobs/fp_applied/withdraw")

        notify_calls = [c for c in popen_calls if any("notify.py" in arg for arg in c)]
        assert len(notify_calls) == 1
        assert "send-raw" in notify_calls[0]

    def test_no_resurface_when_no_waitlisted(self, client: TestClient, popen_calls):
        """No waitlisted jobs at that company → no notification fires."""
        client.post("/board/jobs/fp_applied/withdraw")

        notify_calls = [c for c in popen_calls if any("notify.py" in arg for arg in c)]
        assert notify_calls == []

    def test_idempotent_on_already_withdrawn(self, client: TestClient, popen_calls):
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET stage='withdrawn' WHERE fingerprint='fp_applied'")
        conn.commit()
        conn.close()

        self._seed_waitlisted_sibling(client, "Acme Corp")
        response = client.post("/board/jobs/fp_applied/withdraw")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_applied") == "withdrawn"
        # No notify fires on a no-op withdrawal
        notify_calls = [c for c in popen_calls if any("notify.py" in arg for arg in c)]
        assert notify_calls == []

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/withdraw")
        assert response.status_code == 404


# ── /apply handler synthetic-aware changed_by branch ─────────────────────────


class TestApplySyntheticBranch:
    """The /apply handler must write changed_by='outreach_button' when the
    target row has synthetic=1. Real rows keep the existing changed_by value."""

    def _insert_synthetic(self, client: TestClient, fingerprint: str = "fp_spec_drafted") -> str:
        """Add a synthetic job in materials_drafted stage to the test DB."""
        import uuid

        conn = sqlite3.connect(client._db_path)
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, stage, source, synthetic) "
            "VALUES (?, ?, 'speculative://x', ?, 'PSI', 'materials_drafted', 'web_speculative', 1)",
            (job_id, fingerprint, "[SPEC] Critical Infra Eng"),
        )
        conn.commit()
        conn.close()
        return job_id

    def test_synthetic_apply_writes_outreach_button_changed_by(self, client: TestClient):
        self._insert_synthetic(client, "fp_spec_drafted")
        response = client.post("/board/jobs/fp_spec_drafted/apply")
        assert response.status_code == 200
        assert _fetch_stage(client, "fp_spec_drafted") == "applied"

        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT al.changed_by FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint=? AND al.field_changed='stage' AND al.new_value='applied'",
            ("fp_spec_drafted",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "outreach_button", (
            f"synthetic /apply should write changed_by='outreach_button', got {row[0]!r}"
        )

    def test_real_apply_does_not_use_outreach_button(self, client: TestClient):
        """Move fp_drafted (real, materials_drafted in seed) to applied via /apply."""
        response = client.post("/board/jobs/fp_drafted/apply")
        assert response.status_code == 200
        assert _fetch_stage(client, "fp_drafted") == "applied"

        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT al.changed_by FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint=? AND al.field_changed='stage' AND al.new_value='applied'",
            ("fp_drafted",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] != "outreach_button", f"real /apply must not use outreach_button changed_by, got {row[0]!r}"
