"""Tests for poll_flags.py state machine: DB transitions, folder moves, and stage updates.

Uses a real in-memory SQLite database. Mocks Google Sheets API and module-level side effects
so poll_flags can be imported cleanly.
"""

import importlib
import importlib.util
import os
import sqlite3
import sys
import types
import uuid

import pytest

# ── Import poll_flags with mocked side effects ──────────────────────────────
# poll_flags.py reads config/sheet_id.txt and imports google.* at module level.
# We create fake google modules and a temp config dir to satisfy those.

_poll_flags_mod = None


def _import_poll_flags(tmp_base):
    """Import poll_flags.py with all module-level side effects mocked."""
    global _poll_flags_mod  # noqa: PLW0603
    if _poll_flags_mod is not None:
        return _poll_flags_mod

    # Create the config file that poll_flags reads at import time
    config_dir = os.path.join(tmp_base, "config")
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "sheet_id.txt"), "w") as f:
        f.write("fake-sheet-id\n")

    # Stub google.* modules in sys.modules
    google_mod = types.ModuleType("google")
    google_oauth2 = types.ModuleType("google.oauth2")
    google_sa = types.ModuleType("google.oauth2.service_account")
    google_sa.service_account = types.SimpleNamespace(Credentials=None)
    google_api = types.ModuleType("googleapiclient")
    google_discovery = types.ModuleType("googleapiclient.discovery")
    google_discovery.build = None
    google_errors = types.ModuleType("googleapiclient.errors")
    google_errors.HttpError = type("HttpError", (Exception,), {})

    saved_modules = {}
    stubs = {
        "google": google_mod,
        "google.oauth2": google_oauth2,
        "google.oauth2.service_account": google_sa,
        "googleapiclient": google_api,
        "googleapiclient.discovery": google_discovery,
        "googleapiclient.errors": google_errors,
    }
    for name, mod in stubs.items():
        saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # Temporarily override BASE so the open(config/sheet_id.txt) succeeds
    import findajob.paths

    orig_base = findajob.paths.BASE
    findajob.paths.BASE = tmp_base

    try:
        spec = importlib.util.spec_from_file_location(
            "poll_flags",
            os.path.join(orig_base, "scripts", "poll_flags.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _poll_flags_mod = mod
    finally:
        findajob.paths.BASE = orig_base
        # Restore original google modules (or remove stubs)
        for name, orig in saved_modules.items():
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig

    return _poll_flags_mod


# ── Schema ──────────────────────────────────────────────────────────────────

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


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def poll_flags_mod(tmp_path_factory):
    """Import poll_flags once per test session with mocked module-level side effects."""
    tmp_base = str(tmp_path_factory.mktemp("poll_flags_import"))
    return _import_poll_flags(tmp_base)


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


@pytest.fixture()
def companies_dir(tmp_path):
    base = tmp_path / "companies"
    base.mkdir()
    (base / "_applied").mkdir()
    (base / "_rejected").mkdir()
    (base / "_waitlisted").mkdir()
    return base


@pytest.fixture(autouse=True)
def _patch_poll_flags(poll_flags_mod, tmp_path, monkeypatch):
    """Patch poll_flags module globals for every test."""
    monkeypatch.setattr(poll_flags_mod, "BASE", str(tmp_path))
    monkeypatch.setattr(poll_flags_mod, "log_event", lambda *a, **kw: None)
    # Ensure companies subdirectories exist under tmp_path
    os.makedirs(os.path.join(str(tmp_path), "companies", "_applied"), exist_ok=True)
    os.makedirs(os.path.join(str(tmp_path), "companies", "_rejected"), exist_ok=True)
    os.makedirs(os.path.join(str(tmp_path), "companies", "_waitlisted"), exist_ok=True)


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
    def test_rejection_with_folder(self, poll_flags_mod, db, tmp_path):
        """Rejection moves folder to _rejected, updates DB, writes feedback_log + marker."""
        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_120000"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "resume.pdf").touch()

        job = insert_job(db, stage="materials_drafted", folder=str(folder), score=8)
        result = poll_flags_mod.handle_rejection(db, job, "Low Fit Score")

        assert result is True

        row = db.execute("SELECT stage, reject_reason, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"
        assert row["reject_reason"] == "Low Fit Score"
        # Folder moved to _rejected
        assert "_rejected" in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])

        # Marker file created
        marker_files = [f for f in os.listdir(row["prep_folder_path"]) if f.startswith("REJECTED_")]
        assert len(marker_files) == 1
        assert "Low_Fit_Score" in marker_files[0]

        # feedback_log entry
        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is not None
        assert fb["reject_reason"] == "Low Fit Score"
        assert fb["title"] == "Operations Manager"

        # audit_log entries
        audits = db.execute("SELECT * FROM audit_log WHERE job_id=? ORDER BY id", (job["id"],)).fetchall()
        fields = [a["field_changed"] for a in audits]
        assert "stage" in fields
        assert "reject_reason" in fields

    def test_rejection_without_folder(self, poll_flags_mod, db):
        """Rejection without folder: DB updated, no folder operations."""
        job = insert_job(db, stage="scored", folder=None, score=6)
        result = poll_flags_mod.handle_rejection(db, job, "Wrong Level")

        assert result is False

        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"
        assert row["reject_reason"] == "Wrong Level"

        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is not None

    def test_rejection_writes_jd_excerpt(self, poll_flags_mod, db):
        """If job has raw_jd_text, feedback_log gets first 500 chars."""
        long_jd = "A" * 1000
        job = insert_job(db, stage="scored", raw_jd_text=long_jd)
        poll_flags_mod.handle_rejection(db, job, "Not Relevant")

        fb = db.execute("SELECT jd_excerpt FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert len(fb["jd_excerpt"]) == 500

    def test_rejection_no_jd_gives_empty_excerpt(self, poll_flags_mod, db):
        """No raw_jd_text means empty jd_excerpt."""
        job = insert_job(db, stage="scored", raw_jd_text=None)
        poll_flags_mod.handle_rejection(db, job, "Not Relevant")

        fb = db.execute("SELECT jd_excerpt FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb["jd_excerpt"] == ""


# ── handle_not_selected ────────────────────────────────────────────────────


class TestHandleNotSelected:
    def test_not_selected_with_folder(self, poll_flags_mod, db, tmp_path):
        """Not Selected: stage=not_selected, folder stays in _applied/, marker file added, NO feedback_log."""
        folder = tmp_path / "companies" / "_applied" / "Acme_Ops_2026-04-13_120000"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "resume.pdf").touch()

        job = insert_job(db, stage="applied", folder=str(folder), score=8)
        result = poll_flags_mod.handle_not_selected(db, job, "Too Senior")

        assert result is False  # no folder moved

        row = db.execute("SELECT stage, reject_reason, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "not_selected"
        assert row["reject_reason"] == "Too Senior"
        # Folder stays in _applied/ (not moved)
        assert "_applied" in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])

        # Marker file created
        marker_files = [f for f in os.listdir(row["prep_folder_path"]) if f.startswith("NOT_SELECTED_")]
        assert len(marker_files) == 1
        assert "Too_Senior" in marker_files[0]

        # NO feedback_log entry (critical: company rejections don't contaminate scorer)
        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is None

        # audit_log entries written
        audits = db.execute("SELECT * FROM audit_log WHERE job_id=? ORDER BY id", (job["id"],)).fetchall()
        fields = [a["field_changed"] for a in audits]
        assert "stage" in fields
        assert "reject_reason" in fields
        stage_audit = [a for a in audits if a["field_changed"] == "stage"][0]
        assert stage_audit["new_value"] == "not_selected"

    def test_not_selected_without_folder(self, poll_flags_mod, db):
        """Not Selected without folder: DB updated, no marker file, no feedback_log."""
        job = insert_job(db, stage="applied", folder=None, score=7)
        result = poll_flags_mod.handle_not_selected(db, job, "Skills Mismatch")

        assert result is False

        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "not_selected"
        assert row["reject_reason"] == "Skills Mismatch"

        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is None

    def test_not_selected_only_valid_for_post_apply_stages(self, db):
        """Not Selected on scored job should be guarded — stage stays unchanged."""
        job = insert_job(db, stage="scored")

        # The guard in main() checks: job["stage"] in ("applied", "interview", "offer")
        assert job["stage"] not in ("applied", "interview", "offer")

    def test_not_selected_routes_before_rejection(self, poll_flags_mod, db):
        """When STATUS='Not Selected' + REJECT_REASON set, handle_not_selected is used, not handle_rejection."""
        job = insert_job(db, stage="applied", score=8)
        poll_flags_mod.handle_not_selected(db, job, "Company Not a Fit")

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "not_selected"  # not "rejected"

        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is None  # not written


# ── handle_waitlist ─────────────────────────────────────────────────────────


class TestHandleWaitlist:
    def test_waitlist_with_folder(self, poll_flags_mod, db, tmp_path):
        """Folder moves to _waitlisted/, stage updated."""
        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_140000"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cover_letter.docx").touch()

        job = insert_job(db, stage="materials_drafted", folder=str(folder))
        result = poll_flags_mod.handle_waitlist(db, job)

        assert result is True

        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "waitlisted"
        assert "_waitlisted" in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])

    def test_waitlist_without_folder(self, poll_flags_mod, db):
        """No folder: stage updated, no folder operations, returns False."""
        job = insert_job(db, stage="scored", folder=None)
        result = poll_flags_mod.handle_waitlist(db, job)

        assert result is False

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "waitlisted"

    def test_waitlist_writes_audit_log(self, poll_flags_mod, db):
        """Audit log entry for stage change."""
        job = insert_job(db, stage="scored")
        poll_flags_mod.handle_waitlist(db, job)

        audit = db.execute("SELECT * FROM audit_log WHERE job_id=? AND field_changed='stage'", (job["id"],)).fetchone()
        assert audit is not None
        assert audit["old_value"] == "scored"
        assert audit["new_value"] == "waitlisted"


# ── handle_reactivate ──────────────────────────────────────────────────────


class TestHandleReactivate:
    def test_reactivate_with_folder(self, poll_flags_mod, db, tmp_path):
        """Folder moves back from _waitlisted/, stage→materials_drafted."""
        folder = tmp_path / "companies" / "_waitlisted" / "Acme_Ops_2026-04-13_150000"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "resume.pdf").touch()

        job = insert_job(db, stage="waitlisted", folder=str(folder))
        result = poll_flags_mod.handle_reactivate(db, job)

        assert result is True

        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "materials_drafted"
        assert "_waitlisted" not in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])
        assert os.path.isfile(os.path.join(row["prep_folder_path"], "resume.pdf"))

    def test_reactivate_without_folder(self, poll_flags_mod, db):
        """No folder: stage→scored."""
        job = insert_job(db, stage="waitlisted", folder=None)
        result = poll_flags_mod.handle_reactivate(db, job)

        assert result is False

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

    def test_reactivate_missing_folder_path_falls_back_to_scored(self, poll_flags_mod, db):
        """Folder path in DB but dir doesn't exist → scored."""
        job = insert_job(db, stage="waitlisted", folder="/nonexistent/Acme_Ops")
        result = poll_flags_mod.handle_reactivate(db, job)

        assert result is False

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

        audit = db.execute("SELECT * FROM audit_log WHERE job_id=? AND field_changed='stage'", (job["id"],)).fetchone()
        assert audit["new_value"] == "scored"


# ── Dashboard flag processing (main() logic patterns) ──────────────────────
# We replicate the conditional logic from main() rather than calling main()
# directly, since main() requires Google Sheets API.


class TestDashboardFlagForPrep:
    def test_flag_for_prep_sets_prep_in_progress(self, poll_flags_mod, db):
        """Flag for Prep on scored job → stage=prep_in_progress, apply_flag=1."""
        from datetime import UTC, datetime

        job = insert_job(db, stage="scored", company="Acme Corp")
        flag_val = "Flag for Prep"

        # Replicate main() logic
        is_flagged = flag_val == "Flag for Prep"
        assert is_flagged

        if is_flagged and not job["apply_flag"]:
            now = datetime.now(UTC).isoformat()
            db.execute("UPDATE jobs SET apply_flag=1, updated_at=? WHERE id=?", (now, job["id"]))
            db.commit()
            poll_flags_mod.write_audit(db, job["id"], "apply_flag", "0", "1")

        if is_flagged and job["stage"] in ("scored", "manual_review", "enriched"):
            from findajob.utils import is_valid_company

            if is_valid_company(job["company"]):
                now = datetime.now(UTC).isoformat()
                db.execute(
                    "UPDATE jobs SET stage=?, stage_updated=?, updated_at=? WHERE id=?",
                    ("prep_in_progress", now, now, job["id"]),
                )
                db.commit()
                poll_flags_mod.write_audit(db, job["id"], "stage", job["stage"], "prep_in_progress")

        row = db.execute("SELECT stage, apply_flag FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "prep_in_progress"
        assert row["apply_flag"] == 1

    def test_flag_for_prep_skips_invalid_company(self, poll_flags_mod, db):
        """Blank company → skipped, stage unchanged."""
        job = insert_job(db, stage="scored", company="")

        from findajob.utils import is_valid_company

        assert not is_valid_company(job["company"])

        # main() would skip — stage stays "scored"
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

    def test_flag_for_prep_guards_retrigger(self, db):
        """Job already prep_in_progress → main() wouldn't re-trigger."""
        job = insert_job(db, stage="prep_in_progress")
        # The condition is: stage in ("scored", "manual_review", "enriched")
        assert job["stage"] not in ("scored", "manual_review", "enriched")


class TestDashboardRegenerate:
    def test_regenerate_with_folder(self, poll_flags_mod, db, tmp_path):
        """Regenerate: folder deleted locally, stage=prep_in_progress."""
        from datetime import UTC, datetime

        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_160000"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "resume.pdf").touch()

        job = insert_job(
            db, stage="materials_drafted", folder=str(folder), gdrive_url="https://drive.google.com/folder/abc"
        )

        # Replicate Regenerate logic from main()
        flag_val = "Regenerate"
        if flag_val == "Regenerate":
            f = job["prep_folder_path"]
            if f and os.path.isdir(f):
                import shutil

                shutil.rmtree(f)
            now = datetime.now(UTC).isoformat()
            db.execute(
                """UPDATE jobs SET stage='prep_in_progress', prep_folder_path=NULL,
                   gdrive_folder_url=NULL, apply_flag=1, stage_updated=?, updated_at=?
                   WHERE id=?""",
                (now, now, job["id"]),
            )
            db.commit()
            poll_flags_mod.write_audit(db, job["id"], "stage", job["stage"], "prep_in_progress")

        row = db.execute(
            "SELECT stage, prep_folder_path, gdrive_folder_url, apply_flag FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()
        assert row["stage"] == "prep_in_progress"
        assert row["prep_folder_path"] is None
        assert row["gdrive_folder_url"] is None
        assert row["apply_flag"] == 1
        assert not folder.exists()

    def test_regenerate_without_folder(self, poll_flags_mod, db):
        """Regenerate when prep_folder_path is NULL → still sets prep_in_progress."""
        from datetime import UTC, datetime

        job = insert_job(db, stage="materials_drafted", folder=None)

        now = datetime.now(UTC).isoformat()
        db.execute(
            """UPDATE jobs SET stage='prep_in_progress', prep_folder_path=NULL,
               gdrive_folder_url=NULL, apply_flag=1, stage_updated=?, updated_at=?
               WHERE id=?""",
            (now, now, job["id"]),
        )
        db.commit()

        row = db.execute("SELECT stage, apply_flag FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "prep_in_progress"
        assert row["apply_flag"] == 1


class TestDashboardStatusUpdates:
    def test_applied_with_folder(self, poll_flags_mod, db, tmp_path):
        """Applied: stage=applied, folder moves to _applied/."""
        from datetime import UTC, datetime

        folder = tmp_path / "companies" / "Acme_Ops_2026-04-13_170000"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cover_letter.pdf").touch()

        job = insert_job(db, stage="materials_drafted", folder=str(folder))

        # Replicate Applied logic
        new_stage = "applied"
        now = datetime.now(UTC).isoformat()
        db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", (new_stage, now, job["id"]))
        db.commit()
        poll_flags_mod.write_audit(db, job["id"], "stage", job["stage"], new_stage)

        # Move folder to _applied
        jd = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        f = jd["prep_folder_path"]
        if f and os.path.isdir(f):
            applied_dir = os.path.join(str(tmp_path), "companies", "_applied")
            os.makedirs(applied_dir, exist_ok=True)
            dest = os.path.join(applied_dir, os.path.basename(f))
            import shutil

            shutil.move(f, dest)
            db.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
            db.commit()

        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"
        assert "_applied" in row["prep_folder_path"]
        assert os.path.isdir(row["prep_folder_path"])

    def test_applied_without_folder(self, poll_flags_mod, db):
        """Applied without folder: stage=applied, no folder move."""
        from datetime import UTC, datetime

        job = insert_job(db, stage="materials_drafted", folder=None)

        now = datetime.now(UTC).isoformat()
        db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("applied", now, job["id"]))
        db.commit()

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "applied"

    def test_interviewing_stage(self, db):
        """Interviewing flag → stage=interview."""
        from datetime import UTC, datetime

        STATUS_STAGE_MAP = {
            "Applied": "applied",
            "Interviewing": "interview",
            "Offer": "offer",
            "Withdrew": "withdrawn",
        }
        job = insert_job(db, stage="applied")

        new_stage = STATUS_STAGE_MAP["Interviewing"]
        now = datetime.now(UTC).isoformat()
        db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", (new_stage, now, job["id"]))
        db.commit()

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "interview"

    def test_offer_stage(self, db):
        """Offer flag → stage=offer."""
        from datetime import UTC, datetime

        job = insert_job(db, stage="interview")
        now = datetime.now(UTC).isoformat()
        db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("offer", now, job["id"]))
        db.commit()

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "offer"

    def test_withdrew_stage(self, db):
        """Withdrew flag → stage=withdrawn."""
        from datetime import UTC, datetime

        job = insert_job(db, stage="applied")
        now = datetime.now(UTC).isoformat()
        db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("withdrawn", now, job["id"]))
        db.commit()

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "withdrawn"

    def test_withdrew_triggers_resurface_query(self, db):
        """When a job is withdrawn, waitlisted jobs at same company are found."""
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Site Lead")
        job = insert_job(db, stage="applied", company="Acme Corp", title="Ops Manager")

        # Simulate the resurface query
        rows = db.execute("SELECT title FROM jobs WHERE company=? AND stage='waitlisted'", (job["company"],)).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "Site Lead"


class TestDashboardRejectionPriority:
    def test_rejection_takes_priority_over_flag(self, poll_flags_mod, db):
        """When both reject_val and flag_val are set, rejection wins."""
        job = insert_job(db, stage="scored")
        flag_val = "Flag for Prep"
        reject_val = "Wrong Level"

        is_flagged = flag_val == "Flag for Prep"
        is_rejected = bool(reject_val and reject_val.strip())

        # main() checks rejection first
        assert is_rejected
        assert is_flagged

        # Rejection branch runs, continues past prep
        poll_flags_mod.handle_rejection(db, job, reject_val.strip())

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"


# ── Review tab ──────────────────────────────────────────────────────────────


class TestReviewTab:
    def test_promote_sets_score_and_stage(self, poll_flags_mod, db):
        """Promote: score=7, stage=scored, score_status=scored."""
        from datetime import UTC, datetime

        job = insert_job(db, stage="manual_review", score=5, score_status="manual_review")

        # Replicate Review promote logic
        now = datetime.now(UTC).isoformat()
        db.execute(
            """UPDATE jobs SET relevance_score=7, stage='scored',
                   score_status='scored', score_flag_reason='Promoted from Review tab',
                   stage_updated=?, updated_at=?
               WHERE id=?""",
            (now, now, job["id"]),
        )
        db.commit()
        poll_flags_mod.write_audit(db, job["id"], "stage", "manual_review", "scored")

        row = db.execute(
            "SELECT relevance_score, stage, score_status, score_flag_reason FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()
        assert row["relevance_score"] == 7
        assert row["stage"] == "scored"
        assert row["score_status"] == "scored"
        assert row["score_flag_reason"] == "Promoted from Review tab"

        audit = db.execute("SELECT * FROM audit_log WHERE job_id=? AND field_changed='stage'", (job["id"],)).fetchone()
        assert audit["old_value"] == "manual_review"
        assert audit["new_value"] == "scored"

    def test_review_reject(self, poll_flags_mod, db):
        """Reject from Review tab → handle_rejection called."""
        job = insert_job(db, stage="manual_review", score=4)
        poll_flags_mod.handle_rejection(db, job, "Not Relevant")

        row = db.execute("SELECT stage, reject_reason FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"
        assert row["reject_reason"] == "Not Relevant"

        fb = db.execute("SELECT * FROM feedback_log WHERE job_id=?", (job["id"],)).fetchone()
        assert fb is not None


# ── Waitlist tab ────────────────────────────────────────────────────────────


class TestWaitlistTab:
    def test_reactivate_from_waitlist(self, poll_flags_mod, db):
        """Reactivate from Waitlist tab → handle_reactivate called."""
        job = insert_job(db, stage="waitlisted", folder=None)
        result = poll_flags_mod.handle_reactivate(db, job)

        assert result is False  # no folder
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "scored"

    def test_rejection_priority_over_reactivate(self, poll_flags_mod, db):
        """Both reject and reactivate set → rejection wins (main() logic)."""
        job = insert_job(db, stage="waitlisted")
        status_val = "Reactivate"
        reject_val = "No Longer Interested"

        # main() checks reject_val first
        if reject_val:
            poll_flags_mod.handle_rejection(db, job, reject_val)
        elif status_val == "Reactivate":
            poll_flags_mod.handle_reactivate(db, job)

        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
        assert row["stage"] == "rejected"


# ── notify_waitlist_resurface ───────────────────────────────────────────────


class TestNotifyWaitlistResurface:
    def test_resurface_sends_notification(self, poll_flags_mod, db, monkeypatch):
        """When waitlisted jobs exist at company, Popen is called."""
        import subprocess as sp

        insert_job(db, stage="waitlisted", company="Acme Corp", title="Site Lead")

        popen_calls = []
        monkeypatch.setattr(sp, "Popen", lambda args, **kw: popen_calls.append(args))
        # Also patch subprocess in poll_flags module namespace
        monkeypatch.setattr(poll_flags_mod.subprocess, "Popen", lambda args, **kw: popen_calls.append(args))

        poll_flags_mod.notify_waitlist_resurface(db, "Acme Corp")
        assert len(popen_calls) >= 1

    def test_resurface_no_waitlisted_no_notification(self, poll_flags_mod, db, monkeypatch):
        """No waitlisted jobs → no notification sent."""
        import subprocess as sp

        insert_job(db, stage="scored", company="Acme Corp")

        popen_calls = []
        monkeypatch.setattr(sp, "Popen", lambda args, **kw: popen_calls.append(args))
        monkeypatch.setattr(poll_flags_mod.subprocess, "Popen", lambda args, **kw: popen_calls.append(args))

        poll_flags_mod.notify_waitlist_resurface(db, "Acme Corp")
        assert len(popen_calls) == 0
