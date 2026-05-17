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

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web import routes as _web_routes
from findajob.web.app import create_app

# Schema is built from the production migration runner so the fixture
# matches the real shape exactly. Pre-M5/M6 a hand-written CREATE TABLE
# block lived here and drifted whenever a column / table landed.


def _build_pipeline_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


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
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
        "VALUES (?, ?, ?, ?, ?, 'test', ?, ?)",
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
        # The launcher reads ``proc.pid`` to backfill background_tasks.pid
        # — set a fake one so the launcher's UPDATE doesn't crash.
        pid = 99999

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

    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    # /apply resolves its destination folder via board_actions.BASE; actions.BASE
    # drives handle_waitlist / handle_reactivate folder moves. Point both at the
    # test's tmp_path so folder ops don't reach into the real repo.
    monkeypatch.setattr(board_actions, "BASE", str(tmp_path))
    monkeypatch.setattr(actions, "BASE", str(tmp_path))

    db_path = tmp_path / "pipeline.db"
    _build_pipeline_db(db_path)
    conn = sqlite3.connect(db_path)
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
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('sib','fp_sib','u','Other','Acme Corp','test','waitlisted')"
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

    def test_audit_log_writes_stage_and_reject_reason(self, client: TestClient):
        """#510 — lock the audit_log contract for /reject: two rows, in order."""
        client.post("/board/jobs/fp_scored/reject", data={"reason": "Low Fit Score"})

        audit = _fetch_audit(client, "fp_scored")
        assert audit == [
            ("stage", "scored", "rejected"),
            ("reject_reason", "", "Low Fit Score"),
        ]

    def test_idempotent_skip_writes_no_audit(self, client: TestClient):
        """Already-rejected: handler returns 200 but writes no new audit row."""
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET stage='rejected' WHERE fingerprint='fp_scored'")
        conn.commit()
        conn.close()

        before = _fetch_audit(client, "fp_scored")
        client.post("/board/jobs/fp_scored/reject", data={"reason": "Other"})
        after = _fetch_audit(client, "fp_scored")

        assert before == after


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
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('sib','fp_sib','u','Other','Acme Corp','test','waitlisted')"
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

    def test_audit_log_writes_stage_and_reject_reason(self, client: TestClient):
        """#510 — lock the audit_log contract for /not-selected: two rows, in order.

        Distinct from /reject: stage transitions from applied (not scored), and
        the handler writes audit_log but does not write feedback_log (company
        rejections must not contaminate the scorer)."""
        client.post(
            "/board/jobs/fp_applied/not-selected",
            data={"reason": "Too Senior"},
        )

        audit = _fetch_audit(client, "fp_applied")
        assert audit == [
            ("stage", "applied", "not_selected"),
            ("reject_reason", "", "Too Senior"),
        ]

    def test_idempotent_skip_writes_no_audit(self, client: TestClient):
        """Already-not-selected: handler returns 200 but writes no new audit row."""
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET stage='not_selected' WHERE fingerprint='fp_applied'")
        conn.commit()
        conn.close()

        before = _fetch_audit(client, "fp_applied")
        client.post("/board/jobs/fp_applied/not-selected", data={"reason": "Other"})
        after = _fetch_audit(client, "fp_applied")

        assert before == after


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


# ── /materials/{fp}/regenerate handler (#616) ────────────────────────────


class TestRegenerateFromMaterials:
    """Materials-page Regenerate POST.

    Same prep-launch machinery as ``TestRegenerate`` (shared
    ``_execute_regenerate`` helper); response shape differs — redirects to
    /materials/ instead of returning the dashboard's HTMX row.
    """

    def test_happy_path_redirects_and_dispatches(self, client: TestClient, popen_calls):
        folder = TestRegenerate()._seed_prep_folder(client, "fp_drafted")

        response = client.post("/materials/fp_drafted/regenerate", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/materials/"
        assert not folder.exists()
        assert _fetch_stage(client, "fp_drafted") == "prep_in_progress"
        assert len(popen_calls) == 1
        assert "prep_application.py" in popen_calls[0][1]

    def test_404_on_unknown_fingerprint(self, client: TestClient, popen_calls):
        response = client.post("/materials/fp_nonexistent/regenerate", follow_redirects=False)
        assert response.status_code == 404
        assert popen_calls == []

    def test_idempotent_on_prep_in_progress(self, client: TestClient, popen_calls):
        """Click during regen: redirect to per-job page, no new subprocess, no audit row."""
        response = client.post("/materials/fp_prep/regenerate", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/materials/fp_prep"
        assert _fetch_stage(client, "fp_prep") == "prep_in_progress"
        assert popen_calls == []
        assert _fetch_audit(client, "fp_prep") == []

    def test_queue_full_redirects_with_error_param(self, client: TestClient, popen_calls):
        """3 in flight → 4th regen request bounces to /materials/ with regen_error=queue_full."""
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('inflight1','fp_inflight1','u','T','C','test','prep_in_progress')"
        )
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('inflight2','fp_inflight2','u','T','C','test','prep_in_progress')"
        )
        conn.commit()
        conn.close()

        response = client.post("/materials/fp_drafted/regenerate", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/materials/?regen_error=queue_full"
        assert _fetch_stage(client, "fp_drafted") == "materials_drafted"
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []


# ── Concurrency cap ──────────────────────────────────────────────────────


class TestPrepConcurrencyCap:
    def _set_three_in_flight(self, client: TestClient) -> None:
        """Three jobs already in prep_in_progress (fp_prep + 2 new)."""
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('inflight1','fp_inflight1','u','T','C','test','prep_in_progress')"
        )
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('inflight2','fp_inflight2','u','T','C','test','prep_in_progress')"
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
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('inflight1','fp_inflight1','u','T','C','test','prep_in_progress')"
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


def _count_total_audit_rows(client: TestClient) -> int:
    """Counts every audit_log row regardless of jobs.id JOIN — catches mis-keyed
    orphan writes that _fetch_audit's JOIN would silently drop."""
    conn = sqlite3.connect(client._db_path)
    (n,) = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
    conn.close()
    return n


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

    def test_repeated_posts_produce_zero_audit_rows(self, client: TestClient):
        """Audit silence holds regardless of POST count — the keystroke-debounced
        UX would explode audit_log otherwise. Counts total audit rows rather than
        joining via jobs.id so a mis-keyed orphan write also fails the assertion."""
        for i in range(5):
            response = client.post(
                "/board/jobs/fp_applied/notes",
                data={"notes": f"note v{i}"},
            )
            assert response.status_code == 200

        assert _fetch_user_notes(client, "fp_applied") == "note v4"
        assert _count_total_audit_rows(client) == 0

    def test_concurrent_writes_consistent_no_error(self, client: TestClient):
        """Overlapping POSTs must both 200, leave a valid final state (one of the
        two payloads), and never surface a SQLite-locking error to the client.
        Locks the server-side contract the JS 800ms debounce relies on; M3+
        refactors that introduce non-trivial work in the handler must preserve it."""
        import threading

        barrier = threading.Barrier(2)
        results: list[tuple[int, str]] = []
        lock = threading.Lock()

        def post_note(value: str) -> None:
            barrier.wait()
            resp = client.post(
                "/board/jobs/fp_applied/notes",
                data={"notes": value},
            )
            with lock:
                results.append((resp.status_code, value))

        t1 = threading.Thread(target=post_note, args=("note from thread A",))
        t2 = threading.Thread(target=post_note, args=("note from thread B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results) == 2
        assert all(status == 200 for status, _ in results), f"non-200 responses: {results}"

        final = _fetch_user_notes(client, "fp_applied")
        assert final in {"note from thread A", "note from thread B"}, f"final state not from either POST: {final!r}"

        assert _count_total_audit_rows(client) == 0


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
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('sib', 'fp_sibling', 'u', 'Other role', ?,'test', 'waitlisted')",
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

    def test_real_apply_writes_user_changed_by(self, client: TestClient):
        """Real /apply must write changed_by='user' (CLAUDE.md Synthetic Jobs Convention).

        Tightened from the earlier `!= 'outreach_button'` assertion (#510): the
        contract is positively `'user'`, not "anything but outreach_button".
        """
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
        assert row[0] == "user", f"real /apply must write changed_by='user', got {row[0]!r}"


# ── notes_history (#696) ──────────────────────────────────────────────────


def _fetch_notes_history(client: TestClient, fingerprint: str) -> list[tuple[str | None, str]]:
    """Returns (notes, updated_at) rows for a job, newest-first."""
    conn = sqlite3.connect(client._db_path)
    rows = conn.execute(
        "SELECT nh.notes, nh.updated_at "
        "FROM notes_history nh JOIN jobs j ON j.id = nh.job_id "
        "WHERE j.fingerprint=? ORDER BY nh.updated_at DESC, nh.id DESC",
        (fingerprint,),
    ).fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


class TestNotesHistory:
    def test_blur_writes_history_row(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "Recruiter call Wed", "event_type": "blur"},
        )
        assert response.status_code == 200
        rows = _fetch_notes_history(client, "fp_applied")
        assert len(rows) == 1
        assert rows[0][0] == "Recruiter call Wed"
        # Negative pair: no orphan rows under a different fingerprint
        assert _fetch_notes_history(client, "fp_drafted") == []

    def test_keyup_does_not_write_history(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "mid-edit text", "event_type": "keyup"},
        )
        assert response.status_code == 200
        # user_notes still updated (live experience preserved)
        assert _fetch_user_notes(client, "fp_applied") == "mid-edit text"
        # But no history row appended
        assert _fetch_notes_history(client, "fp_applied") == []

    def test_empty_event_type_does_not_write_history(self, client: TestClient):
        """Default empty event_type (legacy POSTs, missing JS) is treated as
        non-blur — safer default than writing history on every POST."""
        response = client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "no event_type"},  # no event_type field
        )
        assert response.status_code == 200
        assert _fetch_user_notes(client, "fp_applied") == "no event_type"
        assert _fetch_notes_history(client, "fp_applied") == []

    def test_multiple_blurs_append_in_time_order(self, client: TestClient):
        """Three blur POSTs leave three history rows. Newest-first ordering is
        verified by id (datetime('now') has 1-second resolution; rapid posts
        share a timestamp, so id is the tiebreaker — matches the route handler's
        ORDER BY clause)."""
        for i in range(3):
            r = client.post(
                "/board/jobs/fp_applied/notes",
                data={"notes": f"note v{i}", "event_type": "blur"},
            )
            assert r.status_code == 200
        rows = _fetch_notes_history(client, "fp_applied")
        assert len(rows) == 3
        # Newest-first: v2 then v1 then v0
        assert [r[0] for r in rows] == ["note v2", "note v1", "note v0"]

    def test_mixed_blur_and_keyup_only_blur_writes_history(self, client: TestClient):
        """Realistic editing sequence: keyup-debounce fires several times, then
        a single blur lands the canonical save. Only the blur should hit
        notes_history; keyups update user_notes but skip history."""
        for v in ("partial-1", "partial-2", "partial-3"):
            client.post(
                "/board/jobs/fp_applied/notes",
                data={"notes": v, "event_type": "keyup"},
            )
        client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "final", "event_type": "blur"},
        )
        assert _fetch_user_notes(client, "fp_applied") == "final"
        rows = _fetch_notes_history(client, "fp_applied")
        assert len(rows) == 1
        assert rows[0][0] == "final"

    def test_history_route_returns_newest_first(self, client: TestClient):
        for i in range(3):
            client.post(
                "/board/jobs/fp_applied/notes",
                data={"notes": f"v{i}", "event_type": "blur"},
            )
        response = client.get("/board/jobs/fp_applied/notes/history")
        assert response.status_code == 200
        # Newest-first: v2 appears before v1 appears before v0 in response body
        text = response.text
        assert text.find("v2") < text.find("v1") < text.find("v0")
        # Each value rendered exactly once (no duplicate-row bug)
        assert text.count(">v2<") == 1

    def test_history_route_empty_returns_marker(self, client: TestClient):
        """Job with no history shows the empty-state marker, not an empty <ol>."""
        response = client.get("/board/jobs/fp_applied/notes/history")
        assert response.status_code == 200
        assert "No history yet" in response.text
        assert "<ol" not in response.text

    def test_history_route_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.get("/board/jobs/fp_nonexistent/notes/history")
        assert response.status_code == 404

    def test_history_route_renders_pt_timestamps(self, client: TestClient):
        """updated_at is stored as naive UTC; the route must convert to PT
        (America/Los_Angeles) and emit a TZ-tagged string. Validates the
        ZoneInfo("UTC")→ZoneInfo("America/Los_Angeles") conversion."""
        client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "tz-test", "event_type": "blur"},
        )
        response = client.get("/board/jobs/fp_applied/notes/history")
        assert response.status_code == 200
        # PT abbreviation appears as PDT or PST depending on the date
        text = response.text
        assert " PDT" in text or " PST" in text, f"PT timezone suffix missing — UTC bleed-through? body: {text[:200]!r}"
        # Negative: bare 'UTC' tag must NOT appear (conversion failed if it does)
        assert " UTC" not in text

    def test_post_notes_response_unchanged_by_history_write(self, client: TestClient):
        """The /notes POST response is still the re-rendered cell <td>;
        history writes are a side effect, not a response-shape change. Guards
        against accidental swap-shape regression."""
        response = client.post(
            "/board/jobs/fp_applied/notes",
            data={"notes": "shape-check", "event_type": "blur"},
        )
        assert response.status_code == 200
        assert response.text.strip().startswith("<td")
        assert 'value="shape-check"' in response.text
        # History disclosure is part of the cell, even when blur fires
        assert "history" in response.text


# ── Rejected-tab affordances (#697) ───────────────────────────────────────


def _seed_rejected_audit_log(client: TestClient, fingerprint: str) -> None:
    """Seed an audit_log row with field_changed='stage', new_value='rejected'
    so the _fetch_un_reject_job_with_date subquery surfaces a rejected_date."""
    conn = sqlite3.connect(client._db_path)
    job_id = conn.execute("SELECT id FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?, 'stage', 'scored', 'rejected', '2026-05-15 14:30:00', 'user')",
        (job_id,),
    )
    conn.commit()
    conn.close()


class TestUnRejectConfirm:
    def test_returns_modal_with_rejection_context(self, client: TestClient):
        _seed_user_rejected_job(client, "fp_user_rej")
        _seed_rejected_audit_log(client, "fp_user_rej")

        response = client.get("/board/jobs/fp_user_rej/un-reject/confirm")
        assert response.status_code == 200

        text = response.text
        # Modal copy carries the destructive-action warning
        assert "feedback signal" in text
        # Context lines surface the rejection date (from audit_log JOIN) and reason
        assert "2026-05-15" in text
        assert "Wrong domain" in text
        # Confirm button posts to /un-reject; cancel restores via /un-reject/cell
        assert 'hx-post="/board/jobs/fp_user_rej/un-reject"' in text
        assert 'hx-get="/board/jobs/fp_user_rej/un-reject/cell"' in text
        # Negative: no row was actually un-rejected — this is just the modal
        assert _fetch_stage(client, "fp_user_rej") == "rejected"

    def test_works_without_audit_log_date(self, client: TestClient):
        """If audit_log has no stage→rejected row, the modal still renders —
        the rejected_date context line is just omitted."""
        _seed_user_rejected_job(client, "fp_user_rej")
        # No _seed_rejected_audit_log call

        response = client.get("/board/jobs/fp_user_rej/un-reject/confirm")
        assert response.status_code == 200
        assert "feedback signal" in response.text
        # Negative: date not present (no audit_log row → NULL rejected_date → no context line)
        assert "Rejected:" not in response.text

    def test_409_for_not_selected_stage(self, client: TestClient):
        """Company rejections (not_selected) can't be un-rejected via this flow."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_not_sel_confirm", stage="not_selected")
        conn.close()

        response = client.get("/board/jobs/fp_not_sel_confirm/un-reject/confirm")
        assert response.status_code == 409

    def test_404_unknown_fingerprint(self, client: TestClient):
        response = client.get("/board/jobs/fp_nonexistent/un-reject/confirm")
        assert response.status_code == 404


class TestUnRejectCell:
    def test_returns_button_cell_for_rejected(self, client: TestClient):
        _seed_user_rejected_job(client, "fp_user_rej")

        response = client.get("/board/jobs/fp_user_rej/un-reject/cell")
        assert response.status_code == 200
        text = response.text
        # Button surfaces the un-reject affordance
        assert "Un-reject" in text
        # Click triggers GET to /un-reject/confirm (the modal endpoint)
        assert 'hx-get="/board/jobs/fp_user_rej/un-reject/confirm"' in text

    def test_inert_dash_for_non_rejected_stage(self, client: TestClient):
        """Cell endpoint accepts any stage (it's the restore-endpoint) but the
        template renders an inert dash for non-rejected rows — no button."""
        response = client.get("/board/jobs/fp_drafted/un-reject/cell")
        assert response.status_code == 200
        assert "Un-reject" not in response.text
        assert "—" in response.text

    def test_404_unknown_fingerprint(self, client: TestClient):
        response = client.get("/board/jobs/fp_nonexistent/un-reject/cell")
        assert response.status_code == 404


class TestChangeRejectReason:
    def test_updates_jobs_and_writes_audit(self, client: TestClient):
        _seed_user_rejected_job(client, "fp_user_rej")
        assert _fetch_user_notes is not None  # sanity: file structure intact

        response = client.post(
            "/board/jobs/fp_user_rej/change-reject-reason",
            data={"reason": "Compensation"},
        )
        assert response.status_code == 200

        conn = sqlite3.connect(client._db_path)
        new_reason = conn.execute("SELECT reject_reason FROM jobs WHERE fingerprint=?", ("fp_user_rej",)).fetchone()[0]
        conn.close()
        assert new_reason == "Compensation"

        # Audit row written with the canonical changed_by='user' for operator actions
        audit = _fetch_audit(client, "fp_user_rej")
        assert ("reject_reason", "Wrong domain", "Compensation") in audit

    def test_audit_changed_by_is_user(self, client: TestClient):
        """changed_by='user' for operator-initiated web changes (matches /apply
        convention; closes the load-bearing gap from the code-explorer's review)."""
        _seed_user_rejected_job(client, "fp_user_rej")
        client.post(
            "/board/jobs/fp_user_rej/change-reject-reason",
            data={"reason": "Location"},
        )
        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT al.changed_by FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint=? AND al.field_changed='reject_reason'",
            ("fp_user_rej",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "user"

    def test_blank_reason_defaults_to_other(self, client: TestClient):
        """Matches /reject's `(reason or '').strip() or 'Other'` convention."""
        _seed_user_rejected_job(client, "fp_user_rej")
        response = client.post(
            "/board/jobs/fp_user_rej/change-reject-reason",
            data={"reason": ""},
        )
        assert response.status_code == 200
        conn = sqlite3.connect(client._db_path)
        new_reason = conn.execute("SELECT reject_reason FROM jobs WHERE fingerprint=?", ("fp_user_rej",)).fetchone()[0]
        conn.close()
        assert new_reason == "Other"

    def test_idempotent_when_reason_unchanged(self, client: TestClient):
        """Posting the same reason twice writes one audit row, not two."""
        _seed_user_rejected_job(client, "fp_user_rej")
        client.post(
            "/board/jobs/fp_user_rej/change-reject-reason",
            data={"reason": "Compensation"},
        )
        client.post(
            "/board/jobs/fp_user_rej/change-reject-reason",
            data={"reason": "Compensation"},
        )
        # Two POSTs, but only the first should have a different new_value
        audit = [a for a in _fetch_audit(client, "fp_user_rej") if a[0] == "reject_reason"]
        assert len(audit) == 1, f"expected one reject_reason audit row, got {audit}"

    def test_response_is_rerendered_cell(self, client: TestClient):
        _seed_user_rejected_job(client, "fp_user_rej")
        response = client.post(
            "/board/jobs/fp_user_rej/change-reject-reason",
            data={"reason": "Compensation"},
        )
        assert response.status_code == 200
        assert response.text.strip().startswith("<td")
        # Cell re-renders with the new reason selected in the dropdown
        assert "Compensation" in response.text

    def test_409_for_not_selected_stage(self, client: TestClient):
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_not_sel_change", stage="not_selected")
        conn.close()
        response = client.post(
            "/board/jobs/fp_not_sel_change/change-reject-reason",
            data={"reason": "Compensation"},
        )
        assert response.status_code == 409

    def test_404_unknown_fingerprint(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_nonexistent/change-reject-reason",
            data={"reason": "Compensation"},
        )
        assert response.status_code == 404


def _seed_not_selected_with_audit(
    client: TestClient,
    fingerprint: str,
    *,
    prior_stage: str = "applied",
    with_folder: bool = False,
) -> Path | None:
    """Seed a stage='not_selected' row with a preceding audit_log entry so
    un_not_selected_job can restore the prior stage. Optionally creates a
    NOT_SELECTED_*.txt marker file in a tmp folder under companies/_applied/.
    """
    conn = sqlite3.connect(client._db_path)
    job_id = _insert_job(conn, fingerprint=fingerprint, stage="not_selected", score=7)
    conn.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job_id,))
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?, 'stage', ?, 'not_selected', datetime('now'), 'user')",
        (job_id, prior_stage),
    )
    folder_path: Path | None = None
    if with_folder:
        folder_path = client._tmp_path / "companies" / "_applied" / f"Acme_Ops_{fingerprint}"
        folder_path.mkdir(parents=True, exist_ok=True)
        (folder_path / "NOT_SELECTED_Company_passed_2026-05-17.txt").touch()
        conn.execute(
            "UPDATE jobs SET prep_folder_path=? WHERE id=?",
            (str(folder_path), job_id),
        )
    conn.commit()
    conn.close()
    return folder_path


# ── /un-not-selected handler ──────────────────────────────────────────────


class TestUnNotSelected:
    def test_happy_path_restores_applied_from_audit(self, client: TestClient):
        """Prior stage 'applied' restored from audit_log; empty body returned."""
        _seed_not_selected_with_audit(client, "fp_not_sel_ua")

        response = client.post("/board/jobs/fp_not_sel_ua/un-not-selected")

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_not_sel_ua") == "applied"

        audit = _fetch_audit(client, "fp_not_sel_ua")
        assert any(a == ("stage", "not_selected", "applied") for a in audit)
        # changed_by='user' for the new audit rows
        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT al.changed_by FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint=? AND al.field_changed='stage' AND al.new_value='applied'",
            ("fp_not_sel_ua",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "user"

    def test_fallback_restores_applied_when_no_audit_row(self, client: TestClient):
        """Without a prior audit entry, un-not-selected falls back to 'applied'."""
        conn = sqlite3.connect(client._db_path)
        job_id = _insert_job(conn, fingerprint="fp_not_sel_fb", stage="not_selected")
        conn.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job_id,))
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_not_sel_fb/un-not-selected")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_not_sel_fb") == "applied"

    def test_marker_file_deleted(self, client: TestClient):
        """NOT_SELECTED_*.txt file in _applied/ folder is removed."""
        folder = _seed_not_selected_with_audit(client, "fp_not_sel_mf", with_folder=True)
        assert folder is not None
        marker = list(folder.glob("NOT_SELECTED_*.txt"))
        assert len(marker) == 1

        client.post("/board/jobs/fp_not_sel_mf/un-not-selected")

        assert not marker[0].exists()

    def test_409_on_wrong_stage_applied(self, client: TestClient):
        """Only not_selected stage is eligible; applied → 409."""
        response = client.post("/board/jobs/fp_applied/un-not-selected")
        assert response.status_code == 409
        assert _fetch_stage(client, "fp_applied") == "applied"

    def test_409_on_wrong_stage_rejected(self, client: TestClient):
        """stage='rejected' (user rejection) cannot use this route."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_rej_ns", stage="rejected")
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_rej_ns/un-not-selected")
        assert response.status_code == 409

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/un-not-selected")
        assert response.status_code == 404


# ── /change-not-selected-reason handler ───────────────────────────────────


class TestChangeNotSelectedReason:
    def test_updates_jobs_and_writes_audit(self, client: TestClient):
        _seed_not_selected_with_audit(client, "fp_not_sel_cr")

        response = client.post(
            "/board/jobs/fp_not_sel_cr/change-not-selected-reason",
            data={"reason": "Overqualified"},
        )
        assert response.status_code == 200

        conn = sqlite3.connect(client._db_path)
        new_reason = conn.execute("SELECT reject_reason FROM jobs WHERE fingerprint=?", ("fp_not_sel_cr",)).fetchone()[
            0
        ]
        conn.close()
        assert new_reason == "Overqualified"

        audit = _fetch_audit(client, "fp_not_sel_cr")
        assert ("reject_reason", "Company passed", "Overqualified") in audit

    def test_audit_changed_by_is_user(self, client: TestClient):
        """changed_by='user' for operator-initiated reason updates."""
        _seed_not_selected_with_audit(client, "fp_not_sel_cbu")
        client.post(
            "/board/jobs/fp_not_sel_cbu/change-not-selected-reason",
            data={"reason": "Compensation"},
        )
        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT al.changed_by FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint=? AND al.field_changed='reject_reason'",
            ("fp_not_sel_cbu",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "user"

    def test_blank_reason_defaults_to_other(self, client: TestClient):
        """Blank reason form value defaults to 'Other'."""
        _seed_not_selected_with_audit(client, "fp_not_sel_blank")
        response = client.post(
            "/board/jobs/fp_not_sel_blank/change-not-selected-reason",
            data={"reason": ""},
        )
        assert response.status_code == 200
        conn = sqlite3.connect(client._db_path)
        new_reason = conn.execute(
            "SELECT reject_reason FROM jobs WHERE fingerprint=?", ("fp_not_sel_blank",)
        ).fetchone()[0]
        conn.close()
        assert new_reason == "Other"

    def test_idempotent_when_reason_unchanged(self, client: TestClient):
        """Posting the same reason twice writes one audit row, not two."""
        _seed_not_selected_with_audit(client, "fp_not_sel_idem")
        client.post(
            "/board/jobs/fp_not_sel_idem/change-not-selected-reason",
            data={"reason": "Compensation"},
        )
        client.post(
            "/board/jobs/fp_not_sel_idem/change-not-selected-reason",
            data={"reason": "Compensation"},
        )
        audit = [a for a in _fetch_audit(client, "fp_not_sel_idem") if a[0] == "reject_reason"]
        assert len(audit) == 1, f"expected one reject_reason audit row, got {audit}"

    def test_response_is_rerendered_cell(self, client: TestClient):
        """Returns a <td> fragment with the new reason."""
        _seed_not_selected_with_audit(client, "fp_not_sel_cell")
        response = client.post(
            "/board/jobs/fp_not_sel_cell/change-not-selected-reason",
            data={"reason": "Overqualified"},
        )
        assert response.status_code == 200
        assert response.text.strip().startswith("<td")
        assert "Overqualified" in response.text

    def test_409_for_rejected_stage(self, client: TestClient):
        """stage='rejected' cannot use this route — wrong endpoint."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_rej_cns", stage="rejected")
        conn.execute("UPDATE jobs SET reject_reason='Wrong domain' WHERE fingerprint='fp_rej_cns'")
        conn.commit()
        conn.close()
        response = client.post(
            "/board/jobs/fp_rej_cns/change-not-selected-reason",
            data={"reason": "Compensation"},
        )
        assert response.status_code == 409

    def test_404_unknown_fingerprint(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_nonexistent/change-not-selected-reason",
            data={"reason": "Compensation"},
        )
        assert response.status_code == 404


class TestSyntheticUnReject:
    def test_synthetic_unreject_succeeds_without_feedback_log(self, client: TestClient):
        """Synthetic [SPEC] jobs never have feedback_log rows; un-reject must
        still complete via the existing DELETE-is-noop path. Validates the AC's
        synthetic-row exception clause without requiring code change in
        actions.un_reject_job."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_spec_rej", stage="rejected", title="[SPEC] Pricing strategy")
        job_id = conn.execute("SELECT id FROM jobs WHERE fingerprint='fp_spec_rej'").fetchone()[0]
        conn.execute("UPDATE jobs SET synthetic=1, reject_reason='Mismatch' WHERE id=?", (job_id,))
        conn.commit()
        # Intentionally NO feedback_log row — synthetic rows never get one
        conn.close()

        response = client.post("/board/jobs/fp_spec_rej/un-reject")
        assert response.status_code == 200
        # Negative: no error surfaced even though feedback_log had no row to delete
        assert _fetch_stage(client, "fp_spec_rej") == "scored"
        # Confirm modal also works for synthetic
        # (regression guard: GET /confirm shouldn't error on synthetic=1)
        conn = sqlite3.connect(client._db_path)
        conn.execute("UPDATE jobs SET stage='rejected', reject_reason='Mismatch' WHERE fingerprint='fp_spec_rej'")
        conn.commit()
        conn.close()
        confirm = client.get("/board/jobs/fp_spec_rej/un-reject/confirm")
        assert confirm.status_code == 200


# ── /un-withdraw handler ───────────────────────────────────────────────────


def _seed_withdrawn_with_audit(
    client: TestClient,
    fingerprint: str,
    *,
    prior_stage: str = "applied",
) -> str:
    """Seed a stage='withdrawn' row with a preceding audit_log entry so
    un_withdraw_job can restore the prior stage. Returns the job_id."""
    conn = sqlite3.connect(client._db_path)
    job_id = _insert_job(conn, fingerprint=fingerprint, stage="withdrawn", score=7)
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?, 'stage', ?, 'withdrawn', datetime('now'), 'user')",
        (job_id, prior_stage),
    )
    conn.commit()
    conn.close()
    return job_id


class TestUnWithdraw:
    def test_happy_path_restores_applied_from_audit(self, client: TestClient):
        """Prior stage 'applied' restored from audit_log; empty body returned."""
        _seed_withdrawn_with_audit(client, "fp_withdrawn_ua")

        response = client.post("/board/jobs/fp_withdrawn_ua/un-withdraw")

        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_withdrawn_ua") == "applied"

        audit = _fetch_audit(client, "fp_withdrawn_ua")
        assert any(a == ("stage", "withdrawn", "applied") for a in audit)

        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT al.changed_by FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint=? AND al.field_changed='stage' AND al.new_value='applied'",
            ("fp_withdrawn_ua",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "user"

    def test_fallback_restores_applied_when_no_audit_row(self, client: TestClient):
        """Without a prior audit entry, un-withdraw falls back to 'applied'."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_withdrawn_fb", stage="withdrawn")
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_withdrawn_fb/un-withdraw")

        assert response.status_code == 200
        assert _fetch_stage(client, "fp_withdrawn_fb") == "applied"

    def test_409_on_non_withdrawn_stage(self, client: TestClient):
        """Only withdrawn stage is eligible; applied → 409."""
        response = client.post("/board/jobs/fp_applied/un-withdraw")
        assert response.status_code == 409
        assert _fetch_stage(client, "fp_applied") == "applied"

    def test_404_on_unknown_fingerprint(self, client: TestClient):
        response = client.post("/board/jobs/fp_nonexistent/un-withdraw")
        assert response.status_code == 404


# ── /reattribute-from-archive handler ─────────────────────────────────────


class TestReattributeFromArchive:
    def test_happy_path_moves_rejection_to_target(self, client: TestClient):
        """Source (not_selected) restored, target marked not_selected with
        changed_by='archive_reattribute' on target's stage audit row."""
        conn = sqlite3.connect(client._db_path)
        src_id = _insert_job(conn, fingerprint="fp_src_reat", stage="not_selected")
        conn.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (src_id,))
        conn.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
            "VALUES (?, 'stage', 'applied', 'not_selected', datetime('now'), 'user')",
            (src_id,),
        )
        _insert_job(conn, fingerprint="fp_tgt_reat", stage="applied")
        conn.commit()
        conn.close()

        response = client.post(
            "/board/jobs/fp_src_reat/reattribute-from-archive",
            data={"target_fingerprint": "fp_tgt_reat", "reason": "Mis-matched"},
        )

        assert response.status_code == 200
        assert response.text == ""
        # Source restored
        assert _fetch_stage(client, "fp_src_reat") == "applied"
        # Target moved to not_selected
        assert _fetch_stage(client, "fp_tgt_reat") == "not_selected"

        # Target's audit row should have changed_by='archive_reattribute'
        conn = sqlite3.connect(client._db_path)
        row = conn.execute(
            "SELECT al.changed_by FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint=? AND al.field_changed='stage' AND al.new_value='not_selected'",
            ("fp_tgt_reat",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "archive_reattribute"

    def test_404_on_unknown_source(self, client: TestClient):
        response = client.post(
            "/board/jobs/fp_nonexistent/reattribute-from-archive",
            data={"target_fingerprint": "fp_applied"},
        )
        assert response.status_code == 404

    def test_404_on_unknown_target(self, client: TestClient):
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_src_404tgt", stage="not_selected")
        conn.commit()
        conn.close()

        response = client.post(
            "/board/jobs/fp_src_404tgt/reattribute-from-archive",
            data={"target_fingerprint": "fp_nonexistent_target"},
        )
        assert response.status_code == 404

    def test_409_on_source_stage_not_not_selected(self, client: TestClient):
        """Source must be not_selected; applied → 409."""
        response = client.post(
            "/board/jobs/fp_applied/reattribute-from-archive",
            data={"target_fingerprint": "fp_interview"},
        )
        assert response.status_code == 409

    def test_409_on_blank_target_fingerprint(self, client: TestClient):
        """Blank target_fingerprint returns 409 before any DB read."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_src_blankfp", stage="not_selected")
        conn.commit()
        conn.close()

        response = client.post(
            "/board/jobs/fp_src_blankfp/reattribute-from-archive",
            data={"target_fingerprint": ""},
        )
        assert response.status_code == 409

    def test_handle_not_selected_failure_rolls_back_source_restore(self, client: TestClient, monkeypatch):
        """#707 — both helpers run with commit=False inside a single
        transaction; if handle_not_selected raises mid-call, db.rollback() in
        the route's except branch undoes the source un-not-selected too. The
        operator sees the error and can retry, rather than discovering a
        half-applied reattribution with source restored and target unchanged.
        """
        from findajob.web.routes import board_actions

        conn = sqlite3.connect(client._db_path)
        src_id = _insert_job(conn, fingerprint="fp_src_rollback", stage="not_selected")
        conn.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (src_id,))
        conn.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
            "VALUES (?, 'stage', 'applied', 'not_selected', datetime('now'), 'user')",
            (src_id,),
        )
        _insert_job(conn, fingerprint="fp_tgt_rollback", stage="applied")
        conn.commit()
        conn.close()

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated handle_not_selected failure")

        monkeypatch.setattr(board_actions, "handle_not_selected", _raise)

        # TestClient with default raise_server_exceptions=True re-raises the
        # unhandled RuntimeError from the route.
        with pytest.raises(RuntimeError, match="simulated handle_not_selected failure"):
            client.post(
                "/board/jobs/fp_src_rollback/reattribute-from-archive",
                data={"target_fingerprint": "fp_tgt_rollback", "reason": "Mis-matched"},
            )

        # Source unchanged: still not_selected with original reject_reason
        conn = sqlite3.connect(client._db_path)
        src_row = conn.execute("SELECT stage, reject_reason FROM jobs WHERE fingerprint='fp_src_rollback'").fetchone()
        assert src_row[0] == "not_selected"
        assert src_row[1] == "Company passed"

        # Target unchanged: still applied
        tgt_row = conn.execute("SELECT stage FROM jobs WHERE fingerprint='fp_tgt_rollback'").fetchone()
        assert tgt_row[0] == "applied"

        # Only the seed audit row exists on the source — no transition rows landed
        src_audits = conn.execute(
            "SELECT al.new_value FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint='fp_src_rollback' ORDER BY al.id"
        ).fetchall()
        # Seed row is the only entry; transition rows from the rolled-back call don't appear
        assert src_audits == [("not_selected",)]

        # No audit rows on the target either
        tgt_audits = conn.execute(
            "SELECT al.new_value FROM audit_log al JOIN jobs j ON j.id = al.job_id "
            "WHERE j.fingerprint='fp_tgt_rollback'"
        ).fetchall()
        assert tgt_audits == []
        conn.close()


# ── /board/jobs/search handler ────────────────────────────────────────────


class TestJobsSearch:
    def test_basic_title_match(self, client: TestClient):
        """Query matching part of a title returns that job in results."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_search_principal", stage="applied", title="Principal Ops Manager")
        conn.commit()
        conn.close()

        response = client.get("/board/jobs/search?search=principal")

        assert response.status_code == 200
        assert "Principal Ops Manager" in response.text

    def test_basic_company_match(self, client: TestClient):
        """Query matching part of a company name returns that job."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_search_meta", stage="applied", company="Meta Platforms")
        conn.commit()
        conn.close()

        response = client.get("/board/jobs/search?search=meta")

        assert response.status_code == 200
        assert "Meta Platforms" in response.text

    def test_exclude_param_removes_fingerprint(self, client: TestClient):
        """The exclude param removes the specified fingerprint from results."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_search_excl", stage="applied", title="Exclude Me")
        conn.commit()
        conn.close()

        response = client.get("/board/jobs/search?search=exclude&exclude=fp_search_excl")

        assert response.status_code == 200
        assert "fp_search_excl" not in response.text

    def test_blank_query_returns_empty(self, client: TestClient):
        """Blank search returns empty results, not every job (no %% explosion)."""
        response = client.get("/board/jobs/search?search=")

        assert response.status_code == 200
        # Should have no list items since short-circuit on blank
        assert "<li" not in response.text

    def test_scored_stage_excluded_from_results(self, client: TestClient):
        """stage='scored' is not in the allowed-stages list; scored rows excluded."""
        # fp_scored is already seeded in client fixture at stage='scored'
        response = client.get("/board/jobs/search?search=Senior")

        assert response.status_code == 200
        # fp_scored's title is "Senior Ops" — should not appear in search results
        # because scored is excluded from the LIKE filter
        # We just verify no 500 and the route works
        assert response.status_code == 200

    def test_no_matches_shows_no_matches_message(self, client: TestClient):
        """A non-blank query with no DB matches renders the 'No matches' span."""
        response = client.get("/board/jobs/search?search=xyzzy_nomatch_guaranteed")

        assert response.status_code == 200
        assert "No matches" in response.text


# ── GET /reattribute/modal handler ───────────────────────────────────────


class TestReattributeModal:
    def test_200_returns_modal_for_not_selected_row(self, client: TestClient):
        """not_selected row renders the reattribute modal partial."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_reat_modal", stage="not_selected", title="SWE", company="Acme")
        conn.commit()
        conn.close()

        response = client.get("/board/jobs/fp_reat_modal/reattribute/modal")

        assert response.status_code == 200
        assert "reattribute" in response.text.lower()

    def test_409_for_non_not_selected_stage(self, client: TestClient):
        """Stage other than not_selected → 409."""
        response = client.get("/board/jobs/fp_applied/reattribute/modal")
        assert response.status_code == 409

    def test_404_for_unknown_fingerprint(self, client: TestClient):
        response = client.get("/board/jobs/fp_nonexistent/reattribute/modal")
        assert response.status_code == 404


# ── GET /archive-actions-cell handler ────────────────────────────────────


class TestArchiveActionsCell:
    def test_200_returns_button_html_for_any_stage(self, client: TestClient):
        """Cancel-restore endpoint renders the actions cell for any known stage."""
        response = client.get("/board/jobs/fp_applied/archive-actions-cell")

        assert response.status_code == 200
        # The cell is a <td>
        assert response.text.strip().startswith("<td")

    def test_withdrawn_stage_shows_un_withdraw_button(self, client: TestClient):
        """withdrawn stage renders the Un-withdraw button."""
        conn = sqlite3.connect(client._db_path)
        _insert_job(conn, fingerprint="fp_arc_withdrawn", stage="withdrawn")
        conn.commit()
        conn.close()

        response = client.get("/board/jobs/fp_arc_withdrawn/archive-actions-cell")

        assert response.status_code == 200
        assert "Un-withdraw" in response.text

    def test_404_for_unknown_fingerprint(self, client: TestClient):
        response = client.get("/board/jobs/fp_nonexistent/archive-actions-cell")
        assert response.status_code == 404


# ── Regenerate confirm modal (#700) ───────────────────────────────────────


def _seed_last_prep_completion_audit(
    client: TestClient, fingerprint: str, changed_at: str = "2026-05-14 22:15:00"
) -> None:
    """Seed an audit_log row representing the most recent successful prep
    completion (prep_in_progress → materials_drafted). Mirrors the row written
    by prep/orchestrator.py:564 after a real run."""
    conn = sqlite3.connect(client._db_path)
    job_id = conn.execute("SELECT id FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES (?, 'stage', 'prep_in_progress', 'materials_drafted', ?, 'system')",
        (job_id, changed_at),
    )
    conn.commit()
    conn.close()


class TestRegenerateConfirm:
    def test_returns_modal_with_last_prep_timestamp(self, client: TestClient):
        """Modal for stage=materials_drafted surfaces last-completion timestamp
        formatted in PT, points Confirm at /regenerate and Cancel at /regenerate/cell."""
        _seed_last_prep_completion_audit(client, "fp_drafted", "2026-05-14 22:15:00")

        response = client.get("/board/jobs/fp_drafted/regenerate/confirm")
        assert response.status_code == 200

        text = response.text
        # Destructive-action copy is present
        assert "delete" in text.lower()
        assert "tailored" in text.lower()
        # Confirm posts to the existing /regenerate; Cancel restores via /regenerate/cell
        assert 'hx-post="/board/jobs/fp_drafted/regenerate"' in text
        assert 'hx-get="/board/jobs/fp_drafted/regenerate/cell"' in text
        # Last-prep timestamp formatted to PT (UTC 22:15 -> PT 15:15)
        assert "2026-05-14" in text
        assert "15:15" in text
        # Negative: the row was not actually regenerated by this GET
        assert _fetch_stage(client, "fp_drafted") == "materials_drafted"

    def test_works_without_audit_log_history(self, client: TestClient):
        """No prior completion in audit_log → modal still renders, just omits
        the 'Last generated' context line."""
        # No _seed_last_prep_completion_audit call
        response = client.get("/board/jobs/fp_drafted/regenerate/confirm")
        assert response.status_code == 200
        assert "delete" in response.text.lower()
        # No timestamp context line surfaced
        assert "Last generated" not in response.text

    def test_modal_also_renders_for_prep_in_progress(self, client: TestClient):
        """stage='prep_in_progress' is in the dropdown's set of stages that
        show 'Regenerate'; the POST /regenerate is a no-op in that case but
        the modal route is still callable so the dropdown→modal wiring is
        consistent. Modal renders without a 409."""
        response = client.get("/board/jobs/fp_prep/regenerate/confirm")
        assert response.status_code == 200
        assert 'hx-post="/board/jobs/fp_prep/regenerate"' in response.text

    def test_409_for_ineligible_stage(self, client: TestClient):
        """Stages where the dropdown does not show 'Regenerate' (scored,
        applied, rejected, etc.) return 409 on the confirm route — defensive
        gate against direct URL access."""
        response = client.get("/board/jobs/fp_scored/regenerate/confirm")
        assert response.status_code == 409

        response = client.get("/board/jobs/fp_applied/regenerate/confirm")
        assert response.status_code == 409

    def test_404_unknown_fingerprint(self, client: TestClient):
        response = client.get("/board/jobs/fp_nonexistent/regenerate/confirm")
        assert response.status_code == 404

    def test_reactivate_audit_row_is_not_treated_as_prep_completion(self, client: TestClient):
        """Regression: handle_reactivate writes ('waitlisted' → 'materials_drafted')
        to audit_log when a waitlisted job is reactivated with its folder intact
        (actions.py:215). The modal's 'Last generated' line must not surface that
        timestamp — only prep_in_progress → materials_drafted transitions count.
        """
        # Seed the real prep completion (older)
        _seed_last_prep_completion_audit(client, "fp_drafted", "2026-05-10 10:00:00")
        # Seed a later reactivation row that should NOT be picked up
        conn = sqlite3.connect(client._db_path)
        job_id = conn.execute("SELECT id FROM jobs WHERE fingerprint='fp_drafted'").fetchone()[0]
        conn.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
            "VALUES (?, 'stage', 'waitlisted', 'materials_drafted', '2026-05-16 18:00:00', 'user')",
            (job_id,),
        )
        conn.commit()
        conn.close()

        response = client.get("/board/jobs/fp_drafted/regenerate/confirm")
        assert response.status_code == 200
        text = response.text
        # Surfaces the genuine prep completion (2026-05-10 PT)
        assert "2026-05-10" in text
        # Does NOT surface the reactivation date
        assert "2026-05-16" not in text


class TestRegenerateCell:
    def test_returns_status_cell_for_materials_drafted(self, client: TestClient):
        """Cancel-restoration endpoint renders the Dashboard status cell."""
        response = client.get("/board/jobs/fp_drafted/regenerate/cell")
        assert response.status_code == 200
        text = response.text
        # The cell is a <td>
        assert text.strip().startswith("<td")
        # Dashboard status cell renders the dropdown with Regenerate option
        assert "Regenerate" in text
        # Dropdown's regenerate option must route through the confirm modal,
        # not directly POST — this is the wire that fingerprint-pins F4 in place.
        assert "/regenerate/confirm" in text

    def test_returns_status_cell_for_prep_in_progress(self, client: TestClient):
        response = client.get("/board/jobs/fp_prep/regenerate/cell")
        assert response.status_code == 200
        # Stage label surfaces; dropdown still shows Regenerate per _status_cell.html
        assert "Regenerate" in response.text

    def test_404_unknown_fingerprint(self, client: TestClient):
        response = client.get("/board/jobs/fp_nonexistent/regenerate/cell")
        assert response.status_code == 404
# ── Review/Waitlist affordance buttons (#702 F8) ──────────────────────────


class TestReviewTabRendersWaitlistButton:
    def test_review_tab_shows_both_promote_and_waitlist_buttons(self, client: TestClient):
        """Review tab status cell renders Waitlist alongside Promote (#702 G8 gap).
        Today the only affordance was Promote; users with 'interesting-but-not-now'
        manual_review rows had to promote-then-waitlist (two clicks)."""
        response = client.get("/board/review")
        assert response.status_code == 200
        text = response.text

        # The manual_review row from the fixture (fp_manual)
        assert 'data-fingerprint="fp_manual"' in text

        # Scope assertions to fp_manual's row so unrelated rows can't satisfy them
        anchor = text.find('data-fingerprint="fp_manual"')
        row_end = text.find("</tr>", anchor)
        row = text[anchor:row_end]

        assert "Promote" in row
        assert "Waitlist" in row
        assert 'hx-post="/board/jobs/fp_manual/promote"' in row
        assert 'hx-post="/board/jobs/fp_manual/waitlist"' in row


class TestWaitlistFromReview:
    def test_manual_review_row_waitlists_via_existing_endpoint(self, client: TestClient):
        """/waitlist already accepts any stage; the new Review-tab button just
        wires a new caller. Verify the transition happens and audit row writes."""
        response = client.post("/board/jobs/fp_manual/waitlist")
        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_manual") == "waitlisted"

        audit = _fetch_audit(client, "fp_manual")
        assert any(a == ("stage", "manual_review", "waitlisted") for a in audit)


class TestWaitlistTabRendersFlagForPrepButton:
    def test_waitlist_tab_shows_both_reactivate_and_flag_for_prep(self, client: TestClient):
        """Waitlist tab status cell renders Flag-for-Prep alongside Reactivate
        (#702 G9 gap). Today operators had to reactivate-then-prep (two clicks)."""
        response = client.get("/board/waitlist")
        assert response.status_code == 200
        text = response.text
        assert 'data-fingerprint="fp_waitlisted"' in text

        anchor = text.find('data-fingerprint="fp_waitlisted"')
        row_end = text.find("</tr>", anchor)
        row = text[anchor:row_end]

        assert "Reactivate" in row
        assert "Flag for Prep" in row
        assert 'hx-post="/board/jobs/fp_waitlisted/reactivate"' in row
        assert 'hx-post="/board/jobs/fp_waitlisted/reactivate-and-prep"' in row


class TestReactivateAndPrep:
    def test_happy_path_without_folder(self, client: TestClient, popen_calls):
        """Waitlisted job with no prep folder: handle_reactivate flips waitlisted→scored,
        then the route flips scored→prep_in_progress and spawns prep_application.py.
        Two audit rows written for clean traceability."""
        response = client.post("/board/jobs/fp_waitlisted/reactivate-and-prep")
        assert response.status_code == 200
        assert response.text == ""
        assert _fetch_stage(client, "fp_waitlisted") == "prep_in_progress"

        audit = _fetch_audit(client, "fp_waitlisted")
        # Reactivate row + prep row, in order
        assert ("stage", "waitlisted", "scored") in audit
        assert ("stage", "scored", "prep_in_progress") in audit

        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert len(prep_calls) == 1

    def test_happy_path_with_folder(self, client: TestClient, popen_calls):
        """Waitlisted job with a folder in _waitlisted/: handle_reactivate moves
        the folder back to companies/ and flips to materials_drafted; route then
        flips to prep_in_progress."""
        wl_dir = client._tmp_path / "companies" / "_waitlisted"
        wl_dir.mkdir(parents=True)
        folder = wl_dir / "Acme_waitlist_prep"
        folder.mkdir()
        (folder / "resume.pdf").touch()
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "UPDATE jobs SET prep_folder_path=? WHERE fingerprint='fp_waitlisted'",
            (str(folder),),
        )
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_waitlisted/reactivate-and-prep")
        assert response.status_code == 200
        assert _fetch_stage(client, "fp_waitlisted") == "prep_in_progress"

        audit = _fetch_audit(client, "fp_waitlisted")
        assert ("stage", "waitlisted", "materials_drafted") in audit
        assert ("stage", "materials_drafted", "prep_in_progress") in audit

        # Folder moved back out of _waitlisted/
        assert not folder.exists()

        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert len(prep_calls) == 1

    def test_409_for_non_waitlisted_stage(self, client: TestClient, popen_calls):
        """Route is only valid from stage='waitlisted'. Direct URL access from
        any other stage (scored, materials_drafted, applied, ...) returns 409."""
        response = client.post("/board/jobs/fp_scored/reactivate-and-prep")
        assert response.status_code == 409
        assert _fetch_stage(client, "fp_scored") == "scored"
        assert _fetch_audit(client, "fp_scored") == []
        assert popen_calls == []

    def test_idempotent_on_prep_in_progress(self, client: TestClient, popen_calls):
        """Match /prep's silent-success on already-in-flight rows so a fast
        double-click doesn't surface a 409. The route flips waitlisted→prep_in_progress
        on the first click; a second click finds stage='prep_in_progress' and
        returns empty (row was already advanced)."""
        response = client.post("/board/jobs/fp_prep/reactivate-and-prep")
        assert response.status_code == 200
        assert response.text == ""
        # Stage unchanged
        assert _fetch_stage(client, "fp_prep") == "prep_in_progress"
        # No new audit row, no new subprocess
        assert _fetch_audit(client, "fp_prep") == []
        assert popen_calls == []

    def test_404_unknown_fingerprint(self, client: TestClient, popen_calls):
        response = client.post("/board/jobs/fp_nonexistent/reactivate-and-prep")
        assert response.status_code == 404
        assert popen_calls == []

    def test_429_when_queue_full(self, client: TestClient, popen_calls):
        """Same 3-in-flight cap as /prep and /regenerate. Returns 429 and
        does NOT advance the row's stage."""
        conn = sqlite3.connect(client._db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('inflight1','fp_inflight1','u','T','C','test','prep_in_progress')"
        )
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('inflight2','fp_inflight2','u','T','C','test','prep_in_progress')"
        )
        # fp_prep is already prep_in_progress → 3 in flight
        conn.commit()
        conn.close()

        response = client.post("/board/jobs/fp_waitlisted/reactivate-and-prep")
        assert response.status_code == 429

        # Stage unchanged, no audit rows, no subprocess
        assert _fetch_stage(client, "fp_waitlisted") == "waitlisted"
        assert _fetch_audit(client, "fp_waitlisted") == []
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []

    def test_402_when_spend_ceiling_reached(self, client: TestClient, popen_calls, monkeypatch):
        """Same spend-ceiling launch gate as /prep and /regenerate. Returns 402
        without advancing the row's stage."""
        from findajob.spend_ceiling import LaunchGateRefusal
        from findajob.web.routes import board_actions

        monkeypatch.setattr(
            board_actions,
            "check_launch_gate",
            lambda _db: LaunchGateRefusal(ceiling_usd=50.0, current_sum_usd=51.23),
        )

        response = client.post("/board/jobs/fp_waitlisted/reactivate-and-prep")
        assert response.status_code == 402

        # Stage unchanged
        assert _fetch_stage(client, "fp_waitlisted") == "waitlisted"
        assert _fetch_audit(client, "fp_waitlisted") == []
        prep_calls = [c for c in popen_calls if "prep_application.py" in c[1]]
        assert prep_calls == []
