"""Tests for the three ingest-path action helpers:
un_reject_job, reactivate_from_ingest, refresh_active_job."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from findajob import actions as actions_mod

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
    remote_status TEXT DEFAULT 'Unknown',
    known_contacts TEXT DEFAULT '',
    ai_notes TEXT,
    relevance_score INTEGER DEFAULT 7,
    stage TEXT DEFAULT 'scored',
    apply_flag INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '',
    prep_folder_path TEXT,
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
def db(tmp_path, monkeypatch):
    import findajob.utils as utils_mod

    monkeypatch.setattr(utils_mod, "LOG_PATH", str(tmp_path / "events.jsonl"))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@pytest.fixture()
def companies_dir(tmp_path, monkeypatch):
    base = tmp_path / "companies"
    base.mkdir()
    (base / "_rejected").mkdir()
    (base / "_waitlisted").mkdir()
    monkeypatch.setattr(actions_mod, "BASE", str(tmp_path))
    return base


def _insert_job(conn, *, stage="scored", score=5, folder=None, reject_reason=""):
    job_id = str(uuid.uuid4())[:8]
    fp = f"fp_{job_id}"
    conn.execute(
        """INSERT INTO jobs
           (id, fingerprint, url, title, company, relevance_score, stage,
            prep_folder_path, reject_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            fp,
            f"https://example.com/{job_id}",
            "Data Center Manager",
            "Acme Corp",
            score,
            stage,
            folder,
            reject_reason,
        ),
    )
    conn.commit()
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


# ── un_reject_job ────────────────────────────────────────────────────────────


class TestUnRejectJob:
    def test_stage_set_to_scored(self, db, companies_dir):
        job = _insert_job(db, stage="rejected", score=5, reject_reason="Low Fit Score")
        actions_mod.un_reject_job(db, job, {})
        row = db.execute("SELECT stage, relevance_score, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"
        assert row["relevance_score"] == 8
        assert row["reject_reason"] == ""

    def test_feedback_log_rows_deleted(self, db, companies_dir):
        job = _insert_job(db, stage="rejected", reject_reason="Low Fit Score")
        db.execute(
            "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason) VALUES (?,?,?,?,?)",
            (job["id"], "Data Center Manager", "Acme Corp", 5, "Low Fit Score"),
        )
        db.commit()
        assert db.execute("SELECT COUNT(*) FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()[0] == 1
        actions_mod.un_reject_job(db, job, {})
        assert db.execute("SELECT COUNT(*) FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()[0] == 0

    def test_audit_log_entry_written(self, db, companies_dir):
        job = _insert_job(db, stage="rejected", reject_reason="Bad Fit")
        actions_mod.un_reject_job(db, job, {})
        row = db.execute("SELECT * FROM audit_log WHERE job_id=? AND field_changed='stage'", (job["id"],)).fetchone()
        assert row is not None
        assert row["old_value"] == "rejected"
        assert row["new_value"] == "scored"

    def test_folder_moved_from_rejected(self, db, companies_dir):
        rejected_folder = companies_dir / "_rejected" / "Acme_Corp_Data_Center_Manager"
        rejected_folder.mkdir()
        job = _insert_job(db, stage="rejected", folder=str(rejected_folder))
        actions_mod.un_reject_job(db, job, {})
        assert not rejected_folder.exists()
        dest = companies_dir / "Acme_Corp_Data_Center_Manager"
        assert dest.is_dir()
        row = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["prep_folder_path"] == str(dest)

    def test_non_blank_fields_overwritten(self, db, companies_dir):
        job = _insert_job(db, stage="rejected")
        actions_mod.un_reject_job(
            db,
            job,
            {
                "url": "https://new.example.com/job",
                "location": "Austin, TX",
                "remote_status": "Hybrid",
                "raw_jd_text": "New JD text",
                "notes": "New notes",
                "known_contacts": "Jane Doe",
            },
        )
        row = db.execute(
            "SELECT url, location, remote_status, raw_jd_text, ai_notes, known_contacts FROM jobs WHERE id=?",
            (job["id"],),
        ).fetchone()
        assert row["url"] == "https://new.example.com/job"
        assert row["location"] == "Austin, TX"
        assert row["remote_status"] == "Hybrid"
        assert row["raw_jd_text"] == "New JD text"
        assert row["ai_notes"] == "New notes"
        assert row["known_contacts"] == "Jane Doe"

    def test_blank_submitted_field_does_not_clobber_existing(self, db, companies_dir):
        job = _insert_job(db, stage="rejected")
        db.execute("UPDATE jobs SET url='https://original.com/job', location='Menlo Park, CA' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        actions_mod.un_reject_job(db, job, {"url": "", "location": ""})
        row = db.execute("SELECT url, location FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["url"] == "https://original.com/job"
        assert row["location"] == "Menlo Park, CA"


# ── reactivate_from_ingest ───────────────────────────────────────────────────


class TestReactivateFromIngest:
    def test_stage_set_to_scored(self, db, companies_dir):
        job = _insert_job(db, stage="waitlisted", score=7)
        actions_mod.reactivate_from_ingest(db, job, {})
        row = db.execute("SELECT stage, relevance_score FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"
        assert row["relevance_score"] == 8

    def test_folder_moved_from_waitlisted(self, db, companies_dir):
        waitlisted_folder = companies_dir / "_waitlisted" / "Acme_Corp_Data_Center"
        waitlisted_folder.mkdir()
        job = _insert_job(db, stage="waitlisted", folder=str(waitlisted_folder))
        actions_mod.reactivate_from_ingest(db, job, {})
        assert not waitlisted_folder.exists()
        dest = companies_dir / "Acme_Corp_Data_Center"
        assert dest.is_dir()
        row = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["prep_folder_path"] == str(dest)

    def test_audit_log_entry_written(self, db, companies_dir):
        job = _insert_job(db, stage="waitlisted")
        actions_mod.reactivate_from_ingest(db, job, {})
        row = db.execute("SELECT * FROM audit_log WHERE job_id=? AND field_changed='stage'", (job["id"],)).fetchone()
        assert row is not None
        assert row["old_value"] == "waitlisted"
        assert row["new_value"] == "scored"

    def test_non_blank_fields_overwritten(self, db, companies_dir):
        job = _insert_job(db, stage="waitlisted")
        actions_mod.reactivate_from_ingest(db, job, {"url": "https://new.example.com/", "location": "Denver, CO"})
        row = db.execute("SELECT url, location FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["url"] == "https://new.example.com/"
        assert row["location"] == "Denver, CO"

    def test_blank_submitted_field_does_not_clobber_existing(self, db, companies_dir):
        job = _insert_job(db, stage="waitlisted")
        db.execute("UPDATE jobs SET url='https://original.com/' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        actions_mod.reactivate_from_ingest(db, job, {"url": ""})
        row = db.execute("SELECT url FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["url"] == "https://original.com/"


# ── refresh_active_job ───────────────────────────────────────────────────────


class TestRefreshActiveJob:
    def test_low_score_bumped_to_8(self, db, companies_dir):
        job = _insert_job(db, stage="scored", score=5)
        actions_mod.refresh_active_job(db, job, {})
        row = db.execute("SELECT relevance_score FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["relevance_score"] == 8

    def test_score_8_not_changed(self, db, companies_dir):
        job = _insert_job(db, stage="scored", score=8)
        actions_mod.refresh_active_job(db, job, {})
        row = db.execute("SELECT relevance_score FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["relevance_score"] == 8

    def test_manual_review_promoted_to_scored(self, db, companies_dir):
        job = _insert_job(db, stage="manual_review", score=6)
        actions_mod.refresh_active_job(db, job, {})
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

    def test_already_scored_stage_unchanged(self, db, companies_dir):
        job = _insert_job(db, stage="scored", score=9)
        actions_mod.refresh_active_job(db, job, {})
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

    def test_non_blank_fields_overwritten(self, db, companies_dir):
        job = _insert_job(db, stage="scored")
        actions_mod.refresh_active_job(db, job, {"raw_jd_text": "Updated JD"})
        row = db.execute("SELECT raw_jd_text FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["raw_jd_text"] == "Updated JD"
