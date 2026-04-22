"""Unit tests for findajob.actions — stage-transition helpers called from poll_flags
and the web POST handlers.

Every test uses an in-memory SQLite DB and tmp_path for folder operations. The
module-level BASE reference in findajob.actions is monkeypatched so folder
moves land in the test's tmp_path, not the real repo.
"""

import json
import os
import sqlite3
import uuid

import pytest

from findajob import actions, utils

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'test',
    raw_jd_text TEXT,
    relevance_score INTEGER CHECK(relevance_score BETWEEN 1 AND 10),
    score_status TEXT CHECK(score_status IN ('scored', 'manual_review', 'needs_info')),
    score_flag_reason TEXT,
    stage TEXT DEFAULT 'discovered' CHECK(stage IN (
        'discovered', 'enriched', 'scored', 'manual_review',
        'prep_in_progress', 'materials_drafted', 'waitlisted', 'applied',
        'response_received', 'interview', 'offer', 'rejected', 'not_selected', 'withdrawn'
    )),
    stage_updated TEXT,
    apply_flag INTEGER DEFAULT 0,
    prep_folder_path TEXT,
    reject_reason TEXT DEFAULT '',
    fit_score REAL,
    probability_score REAL,
    gdrive_folder_url TEXT,
    remote_status TEXT DEFAULT 'Unknown',
    ai_notes TEXT,
    comp_estimate TEXT DEFAULT '',
    known_contacts TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    dupe_of TEXT DEFAULT ''
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
"""


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _patch_base_and_log(tmp_path, monkeypatch):
    """Redirect BASE and the event log so tests don't touch the real repo."""
    monkeypatch.setattr(actions, "BASE", str(tmp_path))
    monkeypatch.setattr(utils, "LOG_PATH", str(tmp_path / "events.jsonl"))
    os.makedirs(tmp_path / "companies" / "_applied", exist_ok=True)
    os.makedirs(tmp_path / "companies" / "_rejected", exist_ok=True)
    os.makedirs(tmp_path / "companies" / "_waitlisted", exist_ok=True)


def insert_job(
    conn,
    *,
    stage="scored",
    company="Acme Corp",
    title="Operations Manager",
    score=7,
    folder=None,
    raw_jd_text=None,
    score_status="scored",
    apply_flag=0,
    gdrive_url=None,
):
    """Insert a job with sane defaults; returns the row as sqlite3.Row."""
    job_id = str(uuid.uuid4())[:8]
    fp = f"fp_{job_id}"
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, url, title, company, relevance_score,
                             stage, prep_folder_path, raw_jd_text, score_status,
                             apply_flag, gdrive_folder_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            fp,
            f"https://example.com/{job_id}",
            title,
            company,
            score,
            stage,
            folder,
            raw_jd_text,
            score_status,
            apply_flag,
            gdrive_url,
        ),
    )
    conn.commit()
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


# ── handle_rejection ────────────────────────────────────────────────────────


class TestHandleRejection:
    def test_with_folder_moves_and_writes_marker(self, db, tmp_path):
        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_120000"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").touch()

        job = insert_job(db, stage="materials_drafted", folder=str(folder), score=8)
        result = actions.handle_rejection(db, job, "Low Fit Score")

        assert result is True
        row = db.execute("SELECT stage, reject_reason, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"
        assert row["reject_reason"] == "Low Fit Score"
        assert "_rejected" in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])

        markers = [f for f in os.listdir(row["prep_folder_path"]) if f.startswith("REJECTED_")]
        assert len(markers) == 1
        assert "Low_Fit_Score" in markers[0]

    def test_without_folder_returns_false(self, db):
        job = insert_job(db, stage="scored", folder=None, score=6)
        assert actions.handle_rejection(db, job, "Wrong Level") is False

        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"
        assert row["reject_reason"] == "Wrong Level"

    def test_writes_feedback_log_with_jd_excerpt(self, db):
        job = insert_job(db, stage="scored", raw_jd_text="A" * 1000)
        actions.handle_rejection(db, job, "Not Relevant")

        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is not None
        assert fb["reject_reason"] == "Not Relevant"
        assert fb["title"] == "Operations Manager"
        assert len(fb["jd_excerpt"]) == 500

    def test_empty_jd_gives_empty_excerpt(self, db):
        job = insert_job(db, stage="scored", raw_jd_text=None)
        actions.handle_rejection(db, job, "Not Relevant")

        fb = db.execute("SELECT jd_excerpt FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb["jd_excerpt"] == ""

    def test_writes_audit_rows(self, db):
        job = insert_job(db, stage="scored")
        actions.handle_rejection(db, job, "Wrong Level")

        audits = db.execute(
            "SELECT field_changed, old_value, new_value FROM audit_log WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
        fields = [a["field_changed"] for a in audits]
        assert "stage" in fields
        assert "reject_reason" in fields
        stage_audit = next(a for a in audits if a["field_changed"] == "stage")
        assert stage_audit["old_value"] == "scored"
        assert stage_audit["new_value"] == "rejected"

    def test_marker_sanitizes_unsafe_characters(self, db, tmp_path):
        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_120000"
        folder.mkdir(parents=True)
        job = insert_job(db, stage="materials_drafted", folder=str(folder))

        actions.handle_rejection(db, job, "Comp/Role\\Mismatch!")

        dest = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        markers = [f for f in os.listdir(dest) if f.startswith("REJECTED_")]
        assert len(markers) == 1
        # Slashes and exclamation stripped; spaces → underscores
        assert "/" not in markers[0]
        assert "\\" not in markers[0]
        assert "!" not in markers[0]


# ── handle_not_selected ─────────────────────────────────────────────────────


class TestHandleNotSelected:
    def test_keeps_folder_in_applied_drops_marker(self, db, tmp_path):
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_2026-04-13_120000"
        folder.mkdir(parents=True)
        job = insert_job(db, stage="applied", folder=str(folder), score=8)

        result = actions.handle_not_selected(db, job, "Too Senior")

        assert result is False
        row = db.execute("SELECT stage, reject_reason, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "not_selected"
        assert row["reject_reason"] == "Too Senior"
        assert row["prep_folder_path"] == str(folder)

        markers = [f for f in os.listdir(row["prep_folder_path"]) if f.startswith("NOT_SELECTED_")]
        assert len(markers) == 1
        assert "Too_Senior" in markers[0]

    def test_does_not_write_feedback_log(self, db, tmp_path):
        """Company rejections must not contaminate the scorer feedback loop."""
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_2026-04-13_120000"
        folder.mkdir(parents=True)
        job = insert_job(db, stage="applied", folder=str(folder))

        actions.handle_not_selected(db, job, "Skills Mismatch")

        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is None

    def test_without_folder(self, db):
        job = insert_job(db, stage="applied", folder=None)
        assert actions.handle_not_selected(db, job, "Company Passed") is False

        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "not_selected"
        assert row["reject_reason"] == "Company Passed"

    def test_writes_audit_rows(self, db):
        job = insert_job(db, stage="applied")
        actions.handle_not_selected(db, job, "Company Passed")

        audits = db.execute(
            "SELECT field_changed, new_value FROM audit_log WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
        fields = [a["field_changed"] for a in audits]
        assert "stage" in fields
        assert "reject_reason" in fields
        stage_audit = next(a for a in audits if a["field_changed"] == "stage")
        assert stage_audit["new_value"] == "not_selected"


# ── handle_waitlist ─────────────────────────────────────────────────────────


class TestHandleWaitlist:
    def test_with_folder_moves_to_waitlisted(self, db, tmp_path):
        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_140000"
        folder.mkdir(parents=True)
        (folder / "cover_letter.docx").touch()
        job = insert_job(db, stage="materials_drafted", folder=str(folder))

        assert actions.handle_waitlist(db, job) is True

        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "waitlisted"
        assert "_waitlisted" in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])
        assert os.path.isfile(os.path.join(row["prep_folder_path"], "cover_letter.docx"))

    def test_without_folder(self, db):
        job = insert_job(db, stage="scored", folder=None)
        assert actions.handle_waitlist(db, job) is False

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "waitlisted"

    def test_does_not_write_feedback_log(self, db):
        """Waitlisting is deferral, not rejection — no scorer feedback."""
        job = insert_job(db, stage="scored")
        actions.handle_waitlist(db, job)

        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is None

    def test_writes_stage_audit(self, db):
        job = insert_job(db, stage="scored")
        actions.handle_waitlist(db, job)

        audit = db.execute(
            "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
            (job["id"],),
        ).fetchone()
        assert audit["old_value"] == "scored"
        assert audit["new_value"] == "waitlisted"


# ── handle_reactivate ───────────────────────────────────────────────────────


class TestHandleReactivate:
    def test_with_folder_restores_materials_drafted(self, db, tmp_path):
        folder = tmp_path / "companies" / "_waitlisted" / "Acme_Ops_2026-04-13_150000"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").touch()
        job = insert_job(db, stage="waitlisted", folder=str(folder))

        assert actions.handle_reactivate(db, job) is True

        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "materials_drafted"
        assert "_waitlisted" not in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])
        assert os.path.isfile(os.path.join(row["prep_folder_path"], "resume.pdf"))

    def test_without_folder_falls_back_to_scored(self, db):
        job = insert_job(db, stage="waitlisted", folder=None)
        assert actions.handle_reactivate(db, job) is False

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

    def test_missing_folder_path_falls_back_to_scored(self, db):
        """Folder path in DB but directory doesn't exist → scored."""
        job = insert_job(db, stage="waitlisted", folder="/nonexistent/Acme_Ops")
        assert actions.handle_reactivate(db, job) is False

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

    def test_writes_stage_audit(self, db):
        job = insert_job(db, stage="waitlisted", folder=None)
        actions.handle_reactivate(db, job)

        audit = db.execute(
            "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
            (job["id"],),
        ).fetchone()
        assert audit["old_value"] == "waitlisted"
        assert audit["new_value"] == "scored"


# ── notify_waitlist_resurface ───────────────────────────────────────────────


class TestNotifyWaitlistResurface:
    def test_fires_popen_when_waitlisted_exist(self, db, monkeypatch):
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Site Lead")

        popen_calls: list[list[str]] = []
        monkeypatch.setattr(actions.subprocess, "Popen", lambda args, **kw: popen_calls.append(args))

        actions.notify_waitlist_resurface(db, "Acme Corp")

        assert len(popen_calls) == 1
        assert "send-raw" in popen_calls[0]

    def test_no_notification_when_nothing_waitlisted(self, db, monkeypatch):
        insert_job(db, stage="scored", company="Acme Corp")

        popen_calls: list[list[str]] = []
        monkeypatch.setattr(actions.subprocess, "Popen", lambda args, **kw: popen_calls.append(args))

        actions.notify_waitlist_resurface(db, "Acme Corp")

        assert popen_calls == []

    def test_notification_lists_waitlisted_titles(self, db, monkeypatch):
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Site Lead")
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Ops Manager")

        popen_calls: list[list[str]] = []
        monkeypatch.setattr(actions.subprocess, "Popen", lambda args, **kw: popen_calls.append(args))

        actions.notify_waitlist_resurface(db, "Acme Corp")

        body = popen_calls[0][-1]  # last arg is the body
        assert "Site Lead" in body
        assert "Ops Manager" in body


# ── reset_prep_to_scored ────────────────────────────────────────────────────


class TestResetPrepToScored:
    def test_resets_stage_and_clears_folder(self, db):
        job = insert_job(db, stage="prep_in_progress", folder="/tmp/some/path")

        assert actions.reset_prep_to_scored(db, job["id"], reason="test_reason") is True

        row = db.execute("SELECT stage, prep_folder_path, stage_updated FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"
        assert row["prep_folder_path"] is None
        assert row["stage_updated"] is not None

    def test_writes_audit(self, db):
        job = insert_job(db, stage="prep_in_progress")
        actions.reset_prep_to_scored(db, job["id"], reason="unit_test")

        audit = db.execute(
            "SELECT field_changed, old_value, new_value FROM audit_log WHERE job_id=?",
            (job["id"],),
        ).fetchall()
        assert len(audit) == 1
        assert audit[0]["field_changed"] == "stage"
        assert audit[0]["old_value"] == "prep_in_progress"
        assert audit[0]["new_value"] == "scored"

    def test_emits_prep_failed_reset_event(self, db, tmp_path):
        job = insert_job(db, stage="prep_in_progress")
        actions.reset_prep_to_scored(db, job["id"], reason="validation_failed")

        entries = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
        resets = [e for e in entries if e["event"] == "prep_failed_reset"]
        assert len(resets) == 1
        assert resets[0]["job_id"] == job["id"]
        assert resets[0]["reason"] == "validation_failed"

    def test_guards_materials_drafted(self, db):
        job = insert_job(db, stage="materials_drafted", folder="/keep/me")
        assert actions.reset_prep_to_scored(db, job["id"], reason="unused") is False

        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "materials_drafted"
        assert row["prep_folder_path"] == "/keep/me"
        audit = db.execute("SELECT 1 FROM audit_log WHERE job_id=?", (job["id"],)).fetchall()
        assert audit == []

    def test_guards_applied(self, db):
        job = insert_job(db, stage="applied")
        assert actions.reset_prep_to_scored(db, job["id"], reason="unused") is False

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"


# ── promote_to_scored ───────────────────────────────────────────────────────


class TestPromoteToScored:
    def test_sets_score_7_and_stage_scored(self, db):
        job = insert_job(db, stage="manual_review", score=5, score_status="manual_review")
        actions.promote_to_scored(db, job, reason="Promoted from Review tab")

        row = db.execute(
            "SELECT relevance_score, stage, score_status, score_flag_reason FROM jobs WHERE id=?",
            (job["id"],),
        ).fetchone()
        assert row["relevance_score"] == 7
        assert row["stage"] == "scored"
        assert row["score_status"] == "scored"
        assert row["score_flag_reason"] == "Promoted from Review tab"

    def test_default_reason_when_not_provided(self, db):
        job = insert_job(db, stage="manual_review", score=5, score_status="manual_review")
        actions.promote_to_scored(db, job)

        row = db.execute("SELECT score_flag_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["score_flag_reason"] == "Promoted from web UI"

    def test_writes_stage_audit(self, db):
        job = insert_job(db, stage="manual_review", score=5, score_status="manual_review")
        actions.promote_to_scored(db, job)

        audit = db.execute(
            "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
            (job["id"],),
        ).fetchone()
        assert audit["old_value"] == "manual_review"
        assert audit["new_value"] == "scored"

    def test_emits_review_promoted_event(self, db, tmp_path):
        job = insert_job(db, stage="manual_review", score=5, score_status="manual_review")
        actions.promote_to_scored(db, job)

        entries = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
        promoted = [e for e in entries if e["event"] == "review_promoted"]
        assert len(promoted) == 1
        assert promoted[0]["job_id"] == job["id"]
