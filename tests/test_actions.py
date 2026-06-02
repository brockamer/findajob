"""Unit tests for findajob.actions — stage-transition helpers called from the
web POST handlers and the stale-prep watchdog.

Every test uses an in-memory SQLite DB and tmp_path for folder operations. The
module-level BASE reference in findajob.actions is monkeypatched so folder
moves land in the test's tmp_path, not the real repo.
"""

import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from findajob import actions, audit
from findajob.paths import BASE

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
    score_status TEXT CHECK(score_status IN ('scored', 'manual_review')),
    score_flag_reason TEXT,
    stage TEXT DEFAULT 'discovered' CHECK(stage IN (
        'discovered', 'enriched', 'scored', 'manual_review',
        'prep_in_progress', 'briefing_ready', 'materials_drafted', 'waitlisted', 'applied',
        'response_received', 'interview', 'offer', 'rejected', 'not_selected', 'withdrawn',
        'withdrawn_fallback'
    )),
    stage_updated TEXT,
    apply_flag INTEGER DEFAULT 0,
    prep_folder_path TEXT,
    reject_reason TEXT DEFAULT '',
    fit_score REAL,
    probability_score REAL,
    remote_status TEXT DEFAULT 'Unknown',
    ai_notes TEXT,
    comp_estimate TEXT DEFAULT '',
    known_contacts TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    dupe_of TEXT DEFAULT '',
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
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
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
):
    """Insert a job with sane defaults; returns the row as sqlite3.Row."""
    job_id = str(uuid.uuid4())[:8]
    fp = f"fp_{job_id}"
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, url, title, company, relevance_score,
                             stage, prep_folder_path, raw_jd_text, score_status,
                             apply_flag)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    def test_move_target_derived_from_folder_not_base(self, db, tmp_path, monkeypatch):
        """#716 regression: move target must come from the folder being moved
        (its parent IS companies_root by convention), NOT module-level BASE.

        Inversion check: this test re-overrides BASE to a path that has no
        ``companies/`` parent. If ``handle_rejection`` consults BASE to build
        the rejected dir, the assertion below catches it — the move lands
        under ``<wrong_base>/companies/_rejected/``, not the fixture's
        ``tmp_path/companies/_rejected/``. The earlier failure mode (full-
        suite shutil.Error on a colliding repo-rooted dir) was an artifact
        of this same bug; containment is the cleaner invariant.
        """
        wrong_base = tmp_path / "wrong-base"
        wrong_base.mkdir()
        monkeypatch.setattr(actions, "BASE", str(wrong_base))

        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_120000"
        folder.mkdir(parents=True)
        job = insert_job(db, stage="materials_drafted", folder=str(folder), score=8)

        actions.handle_rejection(db, job, "Low Fit Score")

        new_path = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        expected_root = str(tmp_path / "companies" / "_rejected")
        assert new_path.startswith(expected_root), (
            f"move target {new_path} must be under {expected_root}; "
            f"BASE-relative anti-pattern would land it under {wrong_base}"
        )
        assert "wrong-base" not in new_path


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

    def test_changed_by_propagates_to_audit_log(self, db):
        """changed_by keyword passes through to both audit rows for #362 §4.5.2 traceability."""
        job = insert_job(db, stage="applied")
        actions.handle_not_selected(db, job, "Company Passed", changed_by="gmail_rejection_detector")

        audits = db.execute(
            "SELECT field_changed, changed_by FROM audit_log WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
        assert len(audits) == 2
        assert all(a["changed_by"] == "gmail_rejection_detector" for a in audits)

    def test_changed_by_default_preserves_manual_flow(self, db):
        """Omitting changed_by leaves the audit row at the table default 'system'."""
        job = insert_job(db, stage="applied")
        actions.handle_not_selected(db, job, "Company Passed")

        audits = db.execute(
            "SELECT changed_by FROM audit_log WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
        assert len(audits) == 2
        assert all(a["changed_by"] == "system" for a in audits)


# ── un_not_selected_job ─────────────────────────────────────────────────────


class TestUnNotSelectedJob:
    def test_restores_prior_stage_from_audit_log(self, db):
        """Prior stage from audit_log is restored; reject_reason cleared."""
        job = insert_job(db, stage="not_selected")
        # Seed an audit_log row showing the prior stage was 'applied'
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
            "VALUES (?, 'stage', 'applied', 'not_selected', datetime('now'))",
            (job["id"],),
        )
        db.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        restored = actions.un_not_selected_job(db, job)

        assert restored == "applied"
        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"
        assert row["reject_reason"] == ""

    def test_fallback_to_applied_when_no_audit_row(self, db):
        """Without a prior audit_log entry, fallback to 'applied'."""
        job = insert_job(db, stage="not_selected")
        db.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        restored = actions.un_not_selected_job(db, job)

        assert restored == "applied"
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"

    def test_deletes_single_marker_file(self, db, tmp_path):
        """NOT_SELECTED_*.txt markers in the folder are removed."""
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_marker_test"
        folder.mkdir(parents=True)
        marker = folder / "NOT_SELECTED_Company_passed_2026-05-17.txt"
        marker.touch()

        job = insert_job(db, stage="not_selected", folder=str(folder))
        db.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_not_selected_job(db, job)

        assert not marker.exists()

    def test_deletes_multiple_marker_files(self, db, tmp_path):
        """Multiple NOT_SELECTED_*.txt markers are all removed."""
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_multi_marker"
        folder.mkdir(parents=True)
        m1 = folder / "NOT_SELECTED_Company_passed_2026-05-01.txt"
        m2 = folder / "NOT_SELECTED_Company_passed_2026-05-17.txt"
        m1.touch()
        m2.touch()

        job = insert_job(db, stage="not_selected", folder=str(folder))
        db.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_not_selected_job(db, job)

        assert not m1.exists()
        assert not m2.exists()

    def test_reject_reason_cleared(self, db):
        """reject_reason is set to '' after un_not_selected."""
        job = insert_job(db, stage="not_selected")
        db.execute("UPDATE jobs SET reject_reason='Too Senior' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_not_selected_job(db, job)

        row = db.execute("SELECT reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["reject_reason"] == ""

    def test_writes_audit_rows_with_changed_by_user(self, db):
        """Audit rows written with changed_by='user'."""
        job = insert_job(db, stage="not_selected")
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
            "VALUES (?, 'stage', 'applied', 'not_selected', datetime('now'))",
            (job["id"],),
        )
        db.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_not_selected_job(db, job)

        audits = db.execute(
            "SELECT field_changed, old_value, new_value, changed_by FROM audit_log WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
        # The first row was seeded; the new rows are at the end
        new_audits = [a for a in audits if a["changed_by"] == "user"]
        assert len(new_audits) == 2
        stage_audit = next(a for a in new_audits if a["field_changed"] == "stage")
        assert stage_audit["old_value"] == "not_selected"
        assert stage_audit["new_value"] == "applied"
        reason_audit = next(a for a in new_audits if a["field_changed"] == "reject_reason")
        assert reason_audit["new_value"] == ""

    def test_no_folder_succeeds(self, db):
        """Job without a prep_folder_path silently succeeds."""
        job = insert_job(db, stage="not_selected", folder=None)
        db.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        restored = actions.un_not_selected_job(db, job)

        assert restored == "applied"
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"


# ── un_withdraw_job ─────────────────────────────────────────────────────────


class TestUnWithdrawJob:
    def test_restores_prior_stage_from_audit_log(self, db):
        """Prior stage from audit_log is restored (e.g. applied → withdrawn → applied)."""
        job = insert_job(db, stage="withdrawn")
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
            "VALUES (?, 'stage', 'applied', 'withdrawn', datetime('now'))",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        restored = actions.un_withdraw_job(db, job)

        assert restored == "applied"
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"

    def test_restores_interview_stage(self, db):
        """Prior stage of 'interview' is also restorable."""
        job = insert_job(db, stage="withdrawn")
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
            "VALUES (?, 'stage', 'interview', 'withdrawn', datetime('now'))",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        restored = actions.un_withdraw_job(db, job)

        assert restored == "interview"
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "interview"

    def test_fallback_to_applied_when_no_audit_row(self, db):
        """Without a prior audit_log entry, fallback to 'applied'."""
        job = insert_job(db, stage="withdrawn")
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        restored = actions.un_withdraw_job(db, job)

        assert restored == "applied"
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"

    def test_reject_reason_untouched(self, db):
        """withdraw never sets reject_reason; un_withdraw doesn't touch it either."""
        job = insert_job(db, stage="withdrawn")
        # Manually set a reject_reason (shouldn't happen in practice, but must be preserved)
        db.execute("UPDATE jobs SET reject_reason='Preexisting' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_withdraw_job(db, job)

        row = db.execute("SELECT reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["reject_reason"] == "Preexisting"

    def test_writes_audit_row_with_changed_by_user(self, db):
        """Audit row written with old=withdrawn, new=restored, changed_by='user'."""
        job = insert_job(db, stage="withdrawn")
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
            "VALUES (?, 'stage', 'applied', 'withdrawn', datetime('now'))",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_withdraw_job(db, job)

        audits = db.execute(
            "SELECT field_changed, old_value, new_value, changed_by FROM audit_log WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
        new_audits = [a for a in audits if a["changed_by"] == "user"]
        assert len(new_audits) == 1
        assert new_audits[0]["field_changed"] == "stage"
        assert new_audits[0]["old_value"] == "withdrawn"
        assert new_audits[0]["new_value"] == "applied"

    def test_no_folder_side_effects(self, db, tmp_path):
        """No folder is moved; the existing folder path (if any) is unchanged."""
        folder = tmp_path / "companies" / "_applied" / "Acme_Withdraw_test"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").touch()

        job = insert_job(db, stage="withdrawn", folder=str(folder))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_withdraw_job(db, job)

        # Folder still at original location, untouched
        assert folder.exists()
        # prep_folder_path unchanged in DB
        row = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["prep_folder_path"] == str(folder)

    def test_no_feedback_log_row(self, db):
        """un_withdraw never writes or deletes feedback_log rows."""
        job = insert_job(db, stage="withdrawn")
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_withdraw_job(db, job)

        count = db.execute("SELECT COUNT(*) FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()[0]
        assert count == 0


# ── un_apply_job (#699) ─────────────────────────────────────────────────────


class TestUnApplyJob:
    """Reverse a recent /apply: move folder back from _applied/, delete the
    *.applied-YYYY-MM-DD.md snapshot files, flip stage to materials_drafted,
    clear apply_flag, write audit row with changed_by='web_un_apply'."""

    def _seed_applied_with_folder(self, db, tmp_path):
        """Set up the typical post-apply state: stage='applied', folder in
        _applied/ with one real .md + one snapshot sibling."""
        folder = tmp_path / "companies" / "_applied" / "Acme_UnApply_test"
        folder.mkdir(parents=True)
        # Real material
        (folder / "resume.md").write_text("# Resume content")
        # Snapshot sibling (what /apply's snapshot_applied_md_files would have written)
        (folder / "resume.applied-2026-05-17.md").write_text("# Snapshot at apply time")
        # Non-md file is untouched
        (folder / "resume.pdf").write_bytes(b"%PDF")

        job = insert_job(db, stage="applied", folder=str(folder), apply_flag=1)
        return job, folder

    def test_moves_folder_back_to_companies(self, db, tmp_path):
        job, folder = self._seed_applied_with_folder(db, tmp_path)
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        assert not folder.exists()
        new_path = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        # Folder ends up at companies/{base}, not companies/_applied/{base}
        assert os.path.basename(new_path) == "Acme_UnApply_test"
        assert os.path.dirname(new_path).endswith("companies")
        assert os.path.isdir(new_path)
        # Real material survived the round-trip
        assert (os.path.join(new_path, "resume.md")) in [os.path.join(new_path, f) for f in os.listdir(new_path)]
        assert os.path.isfile(os.path.join(new_path, "resume.md"))

    def test_deletes_only_snapshot_files_not_originals(self, db, tmp_path):
        """Snapshot files match the _APPLIED_SNAPSHOT_RE regex
        (.applied-YYYY-MM-DD.md); originals and incidentally-named files survive."""
        job, folder = self._seed_applied_with_folder(db, tmp_path)
        # Adversarial incidentally-named file that a glob *.applied-*.md
        # would clobber but the regex correctly skips (no date suffix shape).
        (folder / "notes.applied-rewrite.md").write_text("# Operator's edit notes")
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        new_path = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        survivors = set(os.listdir(new_path))
        assert "resume.md" in survivors
        assert "resume.pdf" in survivors
        # Incidentally-named (no YYYY-MM-DD shape) survives — regex precision pin
        assert "notes.applied-rewrite.md" in survivors
        # Snapshot deleted
        assert "resume.applied-2026-05-17.md" not in survivors

    def test_apply_flag_cleared(self, db, tmp_path):
        """Spec pin: post-un-apply rows have apply_flag=0 even though
        /prep-completed materials_drafted rows have apply_flag=1. Locks the
        spec so a future reader doesn't 'fix' the asymmetry."""
        job, _ = self._seed_applied_with_folder(db, tmp_path)
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        flag = db.execute("SELECT apply_flag FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert flag == 0

    def test_falls_back_to_materials_drafted_without_audit(self, db, tmp_path):
        """No '… → applied' audit row to read (belt-and-suspenders path): un-apply
        falls back to materials_drafted (#959)."""
        job, _ = self._seed_applied_with_folder(db, tmp_path)
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert stage == "materials_drafted"

    def test_restores_briefing_ready_from_audit(self, db, tmp_path):
        """Applied directly from briefing_ready (Phase B never run): un-apply must
        restore briefing_ready, not the hardcoded materials_drafted (#959). Reading
        the prior stage from audit_log is what #958 made reliable."""
        job, _ = self._seed_applied_with_folder(db, tmp_path)
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
            "VALUES (?, 'stage', 'briefing_ready', 'applied', datetime('now'))",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert stage == "briefing_ready"
        un_apply = db.execute(
            "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND changed_by='web_un_apply'",
            (job["id"],),
        ).fetchone()
        assert un_apply["old_value"] == "applied"
        assert un_apply["new_value"] == "briefing_ready"

    def test_restores_materials_drafted_from_audit(self, db, tmp_path):
        """Applied from materials_drafted (full Phase B path): un-apply restores it
        via the audit lookup (same result as the fallback, but exercised through the
        audit path)."""
        job, _ = self._seed_applied_with_folder(db, tmp_path)
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
            "VALUES (?, 'stage', 'materials_drafted', 'applied', datetime('now'))",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert stage == "materials_drafted"
        un_apply = db.execute(
            "SELECT new_value FROM audit_log WHERE job_id=? AND changed_by='web_un_apply'",
            (job["id"],),
        ).fetchone()
        assert un_apply["new_value"] == "materials_drafted"

    def test_restores_most_recent_applied_on_reapply_cycle(self, db, tmp_path):
        """Apply→un-apply→apply leaves two '… → applied' rows; restoration must use
        the most recent (the second apply's old_value), via changed_at DESC, id DESC."""
        job, _ = self._seed_applied_with_folder(db, tmp_path)
        # Older apply (from materials_drafted), then a newer apply (from briefing_ready),
        # same clock-second so the id tiebreaker is what decides.
        for old in ("materials_drafted", "briefing_ready"):
            db.execute(
                "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) "
                "VALUES (?, 'stage', ?, 'applied', '2026-06-01 10:00:00')",
                (job["id"], old),
            )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert stage == "briefing_ready"

    def test_writes_audit_with_changed_by_web_un_apply(self, db, tmp_path):
        job, _ = self._seed_applied_with_folder(db, tmp_path)
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        audits = db.execute(
            "SELECT field_changed, old_value, new_value, changed_by FROM audit_log WHERE job_id=? ORDER BY id",
            (job["id"],),
        ).fetchall()
        un_apply_rows = [a for a in audits if a["changed_by"] == "web_un_apply"]
        assert len(un_apply_rows) == 1
        assert un_apply_rows[0]["field_changed"] == "stage"
        assert un_apply_rows[0]["old_value"] == "applied"
        assert un_apply_rows[0]["new_value"] == "materials_drafted"

    def test_handles_missing_folder_path_gracefully(self, db):
        """apply_flag was set, stage=applied, but prep_folder_path is NULL
        (folder was never moved). The helper should still flip stage and
        write audit — no crash."""
        job = insert_job(db, stage="applied", folder=None, apply_flag=1)
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        actions.un_apply_job(db, job)

        stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert stage == "materials_drafted"


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

        # Args: [python, notify.py, send-raw, <title>, <body>, --kind, send_raw]
        # Find the body via its position relative to send-raw.
        args = popen_calls[0]
        body = args[args.index("send-raw") + 2]
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


# ── synthetic job guard ─────────────────────────────────────────────────────


def test_handle_rejection_skips_feedback_log_for_synthetic(tmp_path, monkeypatch):
    """A synthetic job rejected by the user must NOT write to feedback_log —
    contaminating the scorer's feedback loop with synthetic signal would be a
    permanent data-quality hit. Real-job rejection still writes."""
    monkeypatch.setattr(actions, "BASE", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    syn_id = str(uuid.uuid4())
    real_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score, synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (syn_id, "syn-fp", "http://x", "[SPEC] PSI Eng", "PSIQuantum", "web_speculative", "applied", 7, 1),
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score, synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (real_id, "real-fp", "http://y", "Real Eng", "RealCo", "greenhouse", "applied", 7, 0),
    )

    syn_job = conn.execute("SELECT * FROM jobs WHERE id=?", (syn_id,)).fetchone()
    real_job = conn.execute("SELECT * FROM jobs WHERE id=?", (real_id,)).fetchone()

    actions.handle_rejection(conn, syn_job, "Fit Mismatch")
    actions.handle_rejection(conn, real_job, "Fit Mismatch")

    syn_count = conn.execute("SELECT COUNT(*) FROM feedback_log WHERE job_id=?", (syn_id,)).fetchone()[0]
    real_count = conn.execute("SELECT COUNT(*) FROM feedback_log WHERE job_id=?", (real_id,)).fetchone()[0]
    assert syn_count == 0, "synthetic rejection must not write feedback_log"
    assert real_count == 1, "real rejection must still write feedback_log"

    # Stage transition still happens for both — synthetic guard only affects feedback_log
    syn_after = conn.execute("SELECT stage FROM jobs WHERE id=?", (syn_id,)).fetchone()
    real_after = conn.execute("SELECT stage FROM jobs WHERE id=?", (real_id,)).fetchone()
    assert syn_after["stage"] == "rejected"
    assert real_after["stage"] == "rejected"


# ── deferred_fs atomic-composition kwarg (#709, supersedes #707 commit=False) ──


class TestActionsDeferredFs:
    """Every helper that touches DB+filesystem must, when called with
    ``deferred_fs=[]``, leave its UPDATEs, audit INSERTs, AND filesystem
    mutations pending: the DB writes stay in the open transaction, and the
    fs ops are appended as closures to the passed list (not executed). The
    contract is verified by issuing ``conn.rollback()`` WITHOUT executing
    the closures and asserting every DB and disk side effect disappeared.

    This is the regression net for #709: previously fs ops ran inline before
    the commit decision, so a rollback reverted the DB while leaving the
    filesystem half-applied.
    """

    def test_un_reject_job_deferred_fs_is_rollback_safe(self, db, tmp_path):
        folder = tmp_path / "companies" / "_rejected" / "Acme_Ops_deferred"
        folder.mkdir(parents=True)
        marker = folder / "REJECTED_Wrong_Level_2026-05-18.txt"
        marker.touch()
        job = insert_job(db, stage="rejected", folder=str(folder), score=2)
        db.execute("UPDATE jobs SET reject_reason='Wrong Level' WHERE id=?", (job["id"],))
        db.execute(
            "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason, jd_excerpt) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job["id"], job["title"], job["company"], 2, "Wrong Level", ""),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        expected_dest = Path(BASE) / "companies" / folder.name

        deferred: list = []
        actions.un_reject_job(db, job, overwrite_fields={}, deferred_fs=deferred)
        # Pre-rollback: caller sees the staged change
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "scored"
        # Pre-rollback: fs op queued but NOT executed — folder still at _rejected/
        assert folder.exists(), "deferred_fs must not execute fs ops"
        assert not expected_dest.exists()
        assert len(deferred) == 1

        db.rollback()

        # Post-rollback: every DB write reversed
        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"
        assert row["reject_reason"] == "Wrong Level"
        fb_count = db.execute("SELECT COUNT(*) FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()[0]
        assert fb_count == 1, "DELETE on feedback_log must roll back too"
        audits = db.execute("SELECT * FROM audit_log WHERE job_id=?", (job["id"],)).fetchall()
        assert len(audits) == 0
        # Post-rollback: filesystem state still pre-call (folder + marker untouched)
        assert folder.exists()
        assert marker.exists()
        assert not expected_dest.exists()

    def test_un_not_selected_job_deferred_fs_is_rollback_safe(self, db, tmp_path):
        # Fixture surgery (#709): the previous version of this test seeded
        # the job without a folder, so there was no fs side effect to assert
        # against. Now seed a folder + marker so the rollback assertion can
        # verify the marker survives an un-not-selected rollback.
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_deferred"
        folder.mkdir(parents=True)
        marker = folder / "NOT_SELECTED_Company_passed_2026-05-18.txt"
        marker.touch()

        job = insert_job(db, stage="not_selected", folder=str(folder))
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
            "VALUES (?, 'stage', 'applied', 'not_selected', datetime('now'), 'user')",
            (job["id"],),
        )
        db.execute("UPDATE jobs SET reject_reason='Company passed' WHERE id=?", (job["id"],))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        seed_audit_count = db.execute("SELECT COUNT(*) FROM audit_log WHERE job_id=?", (job["id"],)).fetchone()[0]

        deferred: list = []
        actions.un_not_selected_job(db, job, deferred_fs=deferred)
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "applied"
        # Pre-rollback: marker delete queued but NOT executed
        assert marker.exists(), "deferred_fs must not execute fs ops"
        assert len(deferred) == 1

        db.rollback()

        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "not_selected"
        assert row["reject_reason"] == "Company passed"
        # Only the seed audit row survives — no new audit rows from the rolled-back call
        post_count = db.execute("SELECT COUNT(*) FROM audit_log WHERE job_id=?", (job["id"],)).fetchone()[0]
        assert post_count == seed_audit_count
        # Post-rollback: marker still on disk — the deferred delete never ran
        assert marker.exists()

    def test_un_not_selected_job_multiple_markers_each_closure_binds_its_own_path(self, db, tmp_path):
        """Regression net for closure lazy-capture in the marker-glob loop.

        With naive ``lambda: os.remove(marker_path)`` the closure binds to the
        loop variable, not the per-iteration value — every closure ends up
        targeting the LAST marker path, so executing the deferred list deletes
        that one file N times and leaves the others on disk. The default-arg
        trick ``def _remove_marker(mp=marker_path)`` defeats this by eager-
        binding ``mp`` at closure definition time.

        Seeding two markers and asserting both queued closures resolve to
        distinct paths catches a regression that a single-marker test cannot.
        """
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_multi"
        folder.mkdir(parents=True)
        marker_a = folder / "NOT_SELECTED_Wrong_Level_2026-05-18.txt"
        marker_b = folder / "NOT_SELECTED_Company_passed_2026-05-19.txt"
        marker_a.touch()
        marker_b.touch()

        job = insert_job(db, stage="not_selected", folder=str(folder))
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
            "VALUES (?, 'stage', 'applied', 'not_selected', datetime('now'), 'user')",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        deferred: list = []
        actions.un_not_selected_job(db, job, deferred_fs=deferred)
        assert len(deferred) == 2, "one closure per marker"
        # Pre-execute: both markers still on disk (deferred_fs blocks fs ops)
        assert marker_a.exists()
        assert marker_b.exists()

        # Execute the deferred ops. Both markers must be deleted — if lazy
        # capture were broken, both closures would target the same path,
        # one os.remove() call would raise FileNotFoundError on the second
        # invocation, AND only one marker would actually disappear.
        db.commit()
        for op in deferred:
            op()

        assert not marker_a.exists()
        assert not marker_b.exists()

    def test_un_withdraw_job_deferred_fs_is_rollback_safe(self, db):
        # un_withdraw_job has no fs ops; the kwarg exists for caller-composition
        # uniformity. Verify the helper still returns the restored stage and
        # leaves the DB writes pending.
        job = insert_job(db, stage="withdrawn")
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
            "VALUES (?, 'stage', 'applied', 'withdrawn', datetime('now'), 'user')",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        seed_audit_count = db.execute("SELECT COUNT(*) FROM audit_log WHERE job_id=?", (job["id"],)).fetchone()[0]

        deferred: list = []
        actions.un_withdraw_job(db, job, deferred_fs=deferred)
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "applied"
        assert deferred == [], "un_withdraw_job has no fs ops"

        db.rollback()

        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "withdrawn"
        post_count = db.execute("SELECT COUNT(*) FROM audit_log WHERE job_id=?", (job["id"],)).fetchone()[0]
        assert post_count == seed_audit_count

    def test_un_interview_job_deferred_fs_is_rollback_safe(self, db):
        job = insert_job(db, stage="interview")
        db.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
            "VALUES (?, 'stage', 'applied', 'interview', datetime('now'), 'user')",
            (job["id"],),
        )
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        seed_audit_count = db.execute("SELECT COUNT(*) FROM audit_log WHERE job_id=?", (job["id"],)).fetchone()[0]

        deferred: list = []
        actions.un_interview_job(db, job, deferred_fs=deferred)
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "applied"
        assert deferred == [], "un_interview_job has no fs ops"

        db.rollback()

        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "interview"
        post_count = db.execute("SELECT COUNT(*) FROM audit_log WHERE job_id=?", (job["id"],)).fetchone()[0]
        assert post_count == seed_audit_count

    def test_handle_not_selected_deferred_fs_is_rollback_safe(self, db, tmp_path):
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_deferred"
        folder.mkdir(parents=True)
        job = insert_job(db, stage="applied", folder=str(folder))

        deferred: list = []
        actions.handle_not_selected(db, job, "Too Senior", deferred_fs=deferred)
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "not_selected"
        # Pre-rollback: marker write queued but NOT executed
        assert list(folder.glob("NOT_SELECTED_*.txt")) == [], "deferred_fs must not execute fs ops"
        assert len(deferred) == 1

        db.rollback()

        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"
        assert row["reject_reason"] == ""
        audits = db.execute("SELECT * FROM audit_log WHERE job_id=?", (job["id"],)).fetchall()
        assert len(audits) == 0
        # Post-rollback: no marker file on disk (the deferred write never ran)
        assert list(folder.glob("NOT_SELECTED_*.txt")) == []

    def test_un_apply_job_deferred_fs_is_rollback_safe(self, db, tmp_path):
        """#726 — un_apply_job participates in the deferred_fs contract.

        Seed an applied row with a folder in `_applied/` containing a
        snapshot `.applied-YYYY-MM-DD.md` file. Verify that ``deferred_fs=[]``
        leaves both the folder and the snapshot on disk pre-execution, that
        rollback restores DB state, and that the deferred closures haven't
        run (so the snapshot is still on disk).
        """
        applied_dir = tmp_path / "companies" / "_applied"
        applied_dir.mkdir(parents=True, exist_ok=True)
        folder = applied_dir / "Acme_Ops_un_apply_deferred"
        folder.mkdir()
        snapshot = folder / "resume.applied-2026-05-18.md"
        snapshot.write_text("snapshot content")

        job = insert_job(db, stage="applied", folder=str(folder))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        deferred: list = []
        actions.un_apply_job(db, job, deferred_fs=deferred)
        # Pre-rollback: DB shows new state
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "materials_drafted"
        # Pre-rollback: fs op queued but NOT executed — folder + snapshot still in place
        assert folder.exists(), "deferred_fs must not execute fs ops"
        assert snapshot.exists()
        assert len(deferred) == 1

        db.rollback()

        # Post-rollback: every DB write reversed
        row = db.execute("SELECT stage, apply_flag FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"
        audits = db.execute("SELECT * FROM audit_log WHERE job_id=?", (job["id"],)).fetchall()
        assert len(audits) == 0
        # Post-rollback: filesystem state still pre-call
        assert folder.exists()
        assert snapshot.exists()

    def test_un_apply_job_multiple_snapshots_single_closure_no_capture_trap(self, db, tmp_path):
        """#726 — un_apply_job collapses per-snapshot deletes into ONE closure
        whose body iterates `glob("*.md")` at execution time (#709 lessons
        learned). Verify that with 2+ snapshots, executing the deferred list
        deletes ALL matching snapshots — a lazy-capture regression would
        only delete the last one because the iteration variable would bind
        to the final value.

        Distinct from `un_not_selected_job`'s 2-marker test in that
        un_apply_job intentionally uses single-closure-with-internal-loop
        rather than N-closures-each-binding-one-path. Both patterns are
        valid; this test locks in the chosen one.
        """
        applied_dir = tmp_path / "companies" / "_applied"
        applied_dir.mkdir(parents=True, exist_ok=True)
        folder = applied_dir / "Acme_Ops_multi_snap"
        folder.mkdir()
        snap_a = folder / "resume.applied-2026-05-18.md"
        snap_b = folder / "cover.applied-2026-05-18.md"
        snap_a.write_text("resume")
        snap_b.write_text("cover")
        # Non-snapshot .md should survive
        unrelated = folder / "briefing.md"
        unrelated.write_text("briefing — not a snapshot, must survive")

        # Monkeypatch BASE so the deferred move targets tmp_path/companies/ —
        # without this, dest is `<repo>/companies/` and the test pollutes the
        # real repo. (Existing un_apply tests use this same pattern.)
        import findajob.actions as actions_mod

        original_base = actions_mod.BASE
        actions_mod.BASE = str(tmp_path)
        try:
            job = insert_job(db, stage="applied", folder=str(folder))
            db.commit()
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

            deferred: list = []
            actions.un_apply_job(db, job, deferred_fs=deferred)
            assert len(deferred) == 1, "single closure body, not per-snapshot"

            db.commit()
            for op in deferred:
                op()
        finally:
            actions_mod.BASE = original_base

        moved_folder = tmp_path / "companies" / folder.name
        assert moved_folder.exists()
        # Both snapshots deleted at the destination
        assert not (moved_folder / snap_a.name).exists()
        assert not (moved_folder / snap_b.name).exists()
        # Non-snapshot .md survived
        assert (moved_folder / unrelated.name).exists()

    def test_reactivate_from_ingest_deferred_fs_is_rollback_safe(self, db, tmp_path):
        """#726 — reactivate_from_ingest participates in the deferred_fs contract.

        Seed a waitlisted row with a folder in `_waitlisted/`. Verify
        ``deferred_fs=[]`` keeps the folder at `_waitlisted/`, then rollback
        restores `stage='waitlisted'` and the folder remains in place.
        """
        wl_dir = tmp_path / "companies" / "_waitlisted"
        wl_dir.mkdir(parents=True, exist_ok=True)
        folder = wl_dir / "Acme_Ops_reactivate_deferred"
        folder.mkdir()

        job = insert_job(db, stage="waitlisted", folder=str(folder))
        db.commit()
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()

        deferred: list = []
        actions.reactivate_from_ingest(db, job, overwrite_fields={}, deferred_fs=deferred)
        # Pre-rollback: DB shows new state
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "scored"
        # Pre-rollback: fs op queued but NOT executed — folder still at _waitlisted/
        assert folder.exists(), "deferred_fs must not execute fs ops"
        assert len(deferred) == 1

        db.rollback()

        # Post-rollback: DB writes reversed
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "waitlisted"
        audits = db.execute("SELECT * FROM audit_log WHERE job_id=?", (job["id"],)).fetchall()
        assert len(audits) == 0
        # Post-rollback: folder still at _waitlisted/
        assert folder.exists()


# ── #358: Fallback queue ─────────────────────────────────────────────────


class TestFallbackQueue:
    """Tests for the three fallback action helpers (#358)."""

    def test_withdraw_as_fallback_sets_stage_and_reason(self, db):
        job = insert_job(db, stage="applied")
        actions.handle_withdraw_as_fallback(db, job, "Better opportunity")
        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "withdrawn_fallback"
        assert row["reject_reason"] == "Better opportunity"

    def test_withdraw_as_fallback_writes_audit(self, db):
        job = insert_job(db, stage="interview")
        actions.handle_withdraw_as_fallback(db, job, "Comp too low")
        audit = db.execute(
            "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
            (job["id"],),
        ).fetchone()
        assert audit["old_value"] == "interview"
        assert audit["new_value"] == "withdrawn_fallback"

    def test_mark_as_fallback_converts_withdrawn(self, db):
        job = insert_job(db, stage="withdrawn")
        actions.mark_as_fallback(db, job)
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "withdrawn_fallback"

    def test_mark_as_fallback_audit_trail(self, db):
        job = insert_job(db, stage="withdrawn")
        actions.mark_as_fallback(db, job)
        audit = db.execute(
            "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
            (job["id"],),
        ).fetchone()
        assert audit["old_value"] == "withdrawn"
        assert audit["new_value"] == "withdrawn_fallback"

    def test_promote_from_fallback_direct_path(self, db):
        """Direct path: applied → withdrawn_fallback → promote restores applied."""
        job = insert_job(db, stage="applied")
        actions.handle_withdraw_as_fallback(db, job, "Better opp")
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "withdrawn_fallback"
        restored = actions.promote_from_fallback(db, job)
        assert restored == "applied"
        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"
        assert row["reject_reason"] == ""

    def test_promote_from_fallback_indirect_path(self, db):
        """Indirect path: interview → withdrawn → mark-as-fallback → promote.

        The critical test: promote must chase through the 'withdrawn' hop
        to find the pre-withdraw stage ('interview'), not stop at 'withdrawn'.
        """
        job = insert_job(db, stage="interview")
        # Step 1: withdraw (normal withdraw, e.g. via Applied tab)
        now = "2026-01-01T00:00:00"
        db.execute("UPDATE jobs SET stage='withdrawn', updated_at=? WHERE id=?", (now, job["id"]))
        db.commit()
        audit.write_audit(db, job["id"], "stage", "interview", "withdrawn")
        # Step 2: mark as fallback from Archive
        job_row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        actions.mark_as_fallback(db, job_row)
        assert db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()["stage"] == "withdrawn_fallback"
        # Step 3: promote — must restore to 'interview', NOT 'withdrawn'
        job_row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        restored = actions.promote_from_fallback(db, job_row)
        assert restored == "interview", (
            f"promote_from_fallback should chase through 'withdrawn' to find 'interview', got '{restored}'"
        )
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "interview"

    def test_promote_from_fallback_no_audit_falls_back_to_applied(self, db):
        """Edge case: no audit trail → defaults to 'applied'."""
        job = insert_job(db, stage="withdrawn_fallback")
        restored = actions.promote_from_fallback(db, job)
        assert restored == "applied"
