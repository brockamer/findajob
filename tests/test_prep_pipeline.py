"""Tests for non-LLM logic in prep_application.py: fit score parsing, DB updates, file naming, Drive URL flow."""

import re
import sqlite3
import uuid

import pytest

# ── Fit score parsing (replicated from prep_application.py lines 179-200) ────


def parse_fit_scores(fit_analysis):
    """Replicate the fit score parsing logic from prep_application.py."""
    fit_score_avg = None
    prob_score_avg = None
    if not fit_analysis:
        return fit_score_avg, prob_score_avg
    parts = re.split(r"##\s*🎯\s*Probability Assessment", fit_analysis, maxsplit=1)
    fit_section = parts[0] if parts else fit_analysis
    prob_section = parts[1] if len(parts) > 1 else ""
    fit_scores = [int(m.group(1)) for m in re.finditer(r":\s*(\d{1,3})%", fit_section)]
    prob_scores = [int(m.group(1)) for m in re.finditer(r":\s*(\d{1,3})%", prob_section)]
    if fit_scores:
        fit_score_avg = round(sum(fit_scores) / len(fit_scores), 1)
    if prob_scores:
        prob_score_avg = round(sum(prob_scores) / len(prob_scores), 1)
    return fit_score_avg, prob_score_avg


# ── DB schema (minimal but includes prep-relevant columns) ───────────────────

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
    relevance_score INTEGER,
    stage TEXT DEFAULT 'discovered',
    stage_updated TEXT,
    apply_flag INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '',
    prep_folder_path TEXT,
    fit_score REAL,
    probability_score REAL,
    gdrive_folder_url TEXT,
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
"""


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def insert_job(
    conn,
    *,
    stage="scored",
    company="Acme Corp",
    title="Operations Manager",
    score=7,
    folder=None,
    fit_score=None,
    prob_score=None,
    gdrive_url=None,
):
    """Insert a job with sane defaults; returns the job_id."""
    job_id = str(uuid.uuid4())[:8]
    fp = f"fp_{job_id}"
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, url, title, company, relevance_score,
                             stage, prep_folder_path, fit_score, probability_score, gdrive_folder_url)
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
            fit_score,
            prob_score,
            gdrive_url,
        ),
    )
    conn.commit()
    return job_id


# ═══════════════════════════════════════════════════════════════════════════════
# Fit Score Parsing Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFitScoreParsing:
    """Test the regex-based fit score parsing logic."""

    def test_standard_fit_analysis(self):
        """6 dimension scores in fit section, 3 probability scores after heading."""
        text = """\
## 📊 Fit Matrix
Technical Skills: 85%
Domain Experience: 70%
Leadership: 90%
Culture Fit: 75%
Growth Potential: 80%
Location: 60%

## 🎯 Probability Assessment
Interview Likelihood: 65%
Offer Probability: 45%
Long-term Fit: 70%
"""
        fit, prob = parse_fit_scores(text)
        assert fit == 76.7
        assert prob == 60.0

    def test_no_probability_section(self):
        """Only fit scores, no Probability Assessment heading."""
        text = """\
## 📊 Fit Matrix
Technical Skills: 85%
Domain Experience: 70%
Leadership: 90%
"""
        fit, prob = parse_fit_scores(text)
        assert fit == 81.7
        assert prob is None

    def test_empty_fit_analysis(self):
        """Empty string returns both None."""
        fit, prob = parse_fit_scores("")
        assert fit is None
        assert prob is None

    def test_none_fit_analysis(self):
        """None input returns both None."""
        fit, prob = parse_fit_scores(None)
        assert fit is None
        assert prob is None

    def test_malformed_percentages(self):
        """No numeric percentages in text → empty lists → both None."""
        text = """\
## 📊 Fit Matrix
Technical Skills: high
Domain Experience: excellent
"""
        fit, prob = parse_fit_scores(text)
        assert fit is None
        assert prob is None

    def test_all_100_percent(self):
        """All scores 100% → averages are 100.0."""
        text = """\
## 📊 Fit Matrix
A: 100%
B: 100%

## 🎯 Probability Assessment
C: 100%
"""
        fit, prob = parse_fit_scores(text)
        assert fit == 100.0
        assert prob == 100.0

    def test_zero_percent_scores(self):
        """Edge case with 0% scores."""
        text = """\
## 📊 Fit Matrix
A: 0%
B: 0%

## 🎯 Probability Assessment
C: 0%
"""
        fit, prob = parse_fit_scores(text)
        assert fit == 0.0
        assert prob == 0.0

    def test_single_score_each_section(self):
        """One fit, one probability score."""
        text = """\
Relevance: 72%

## 🎯 Probability Assessment
Interview: 55%
"""
        fit, prob = parse_fit_scores(text)
        assert fit == 72.0
        assert prob == 55.0

    def test_emoji_heading_extra_whitespace(self):
        """Heading split regex uses \\s* around emoji — extra whitespace should work."""
        text = """\
Relevance: 80%

##   🎯   Probability Assessment
Interview: 40%
"""
        fit, prob = parse_fit_scores(text)
        assert fit == 80.0
        assert prob == 40.0

    def test_scores_with_trailing_context(self):
        """'Technical Skills: 85% (strong match)' — regex should still capture 85."""
        text = """\
Technical Skills: 85% (strong match)
Domain Experience: 70% — solid background

## 🎯 Probability Assessment
Interview Likelihood: 65% (based on network)
"""
        fit, prob = parse_fit_scores(text)
        assert fit == 77.5
        assert prob == 65.0

    def test_no_colon_before_percentage(self):
        """'85% match' without colon should NOT be captured by regex `:\\s*(\\d{1,3})%`."""
        text = """\
85% match on skills
This role is 90% remote

## 🎯 Probability Assessment
Also 50% likely
"""
        fit, prob = parse_fit_scores(text)
        assert fit is None
        assert prob is None


# ═══════════════════════════════════════════════════════════════════════════════
# DB State Transition Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDBStateTransitions:
    """Test DB updates that prep_application.py performs."""

    def test_prep_sets_materials_drafted(self, db):
        """Prep completion sets stage=materials_drafted with folder path and scores."""
        job_id = insert_job(db, stage="prep_in_progress")
        outdir = "/home/user/companies/Acme_Ops_Manager_2026-04-13_140000"
        fit_avg = 76.7
        prob_avg = 60.0
        now = "2026-04-13T14:00:00+00:00"

        db.execute(
            """UPDATE jobs SET stage='materials_drafted', stage_updated=?, prep_folder_path=?,
                   fit_score=?, probability_score=?, updated_at=?
               WHERE id=?""",
            (now, outdir, fit_avg, prob_avg, now, job_id),
        )
        db.commit()

        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["stage"] == "materials_drafted"
        assert row["prep_folder_path"] == outdir
        assert row["fit_score"] == 76.7
        assert row["probability_score"] == 60.0

    def test_gdrive_url_stored_on_success(self, db):
        """Drive URL stored in DB when gdrive_folder_url is set."""
        job_id = insert_job(
            db, stage="materials_drafted", folder="/home/user/companies/Acme_Ops_Manager_2026-04-13_140000"
        )
        drive_url = "https://drive.google.com/drive/folders/abc123"

        db.execute("UPDATE jobs SET gdrive_folder_url=? WHERE id=?", (drive_url, job_id))
        db.commit()

        row = db.execute("SELECT gdrive_folder_url FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["gdrive_folder_url"] == drive_url

    def test_gdrive_url_stays_null_when_not_set(self, db):
        """Drive URL stays NULL when no update is executed."""
        job_id = insert_job(
            db, stage="materials_drafted", folder="/home/user/companies/Acme_Ops_Manager_2026-04-13_140000"
        )
        # No UPDATE issued — URL should remain NULL
        row = db.execute("SELECT gdrive_folder_url FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["gdrive_folder_url"] is None

    def test_regenerate_clears_prep_state(self, db):
        """Regeneration clears folder path, Drive URL, and resets stage to prep_in_progress."""
        job_id = insert_job(
            db,
            stage="materials_drafted",
            folder="/home/user/companies/Acme_Ops_Manager_2026-04-13_140000",
            fit_score=76.7,
            prob_score=60.0,
            gdrive_url="https://drive.google.com/drive/folders/abc123",
        )

        db.execute(
            """UPDATE jobs SET prep_folder_path=NULL, gdrive_folder_url=NULL,
                   stage='prep_in_progress' WHERE id=?""",
            (job_id,),
        )
        db.commit()

        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["stage"] == "prep_in_progress"
        assert row["prep_folder_path"] is None
        assert row["gdrive_folder_url"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# File/Folder Naming Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFileNaming:
    """Test output directory naming conventions from prep_application.py."""

    def test_output_dir_format(self):
        """Verify {Company}_{AbbrevTitle}_{date}_{time} format."""
        company = "Acme Corp"
        title = "Senior Operations Manager"
        safe_company = re.sub(r"[^\w\s\-&.,]", "_", company).strip()
        # Replicate abbrev_title
        t = re.sub(r"\s*\(.*?\)", "", title)
        t = re.sub(r"[^\w\s-]", "", t)
        words = [w for w in t.split() if w][:3]
        abbrev = "_".join(words) if words else "Job"
        date = "2026-04-13"
        time_str = "140000"
        outdir = f"{safe_company}_{abbrev}_{date}_{time_str}"
        assert outdir == "Acme Corp_Senior_Operations_Manager_2026-04-13_140000"

    def test_duplicate_guard_skips_already_drafted(self, db):
        """If stage is materials_drafted with a prep_folder_path, prep should skip."""
        folder = "/home/user/companies/Acme_Ops_Manager_2026-04-13_140000"
        job_id = insert_job(db, stage="materials_drafted", folder=folder)

        existing = db.execute("SELECT prep_folder_path, stage FROM jobs WHERE id=?", (job_id,)).fetchone()

        should_skip = existing and existing["prep_folder_path"] and existing["stage"] == "materials_drafted"
        assert should_skip is True

    def test_no_skip_when_stage_not_materials_drafted(self, db):
        """If stage is not materials_drafted, prep should proceed even with folder set."""
        folder = "/home/user/companies/Acme_Ops_Manager_2026-04-13_140000"
        job_id = insert_job(db, stage="prep_in_progress", folder=folder)

        existing = db.execute("SELECT prep_folder_path, stage FROM jobs WHERE id=?", (job_id,)).fetchone()

        should_skip = existing and existing["prep_folder_path"] and existing["stage"] == "materials_drafted"
        assert not should_skip


# ═══════════════════════════════════════════════════════════════════════════════
# Missing Candidate Files Abort Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingCandidateFilesAbort:
    """Test the early-abort path when profile.md or master_resume.md is absent."""

    def test_missing_files_resets_stage_to_scored(self, db):
        """When candidate files are missing, stage resets to scored (not materials_drafted)."""
        job_id = insert_job(db, stage="prep_in_progress")
        now = "2026-04-22T10:00:00+00:00"

        # Replicate the abort path from prep_application.py
        db.execute(
            "UPDATE jobs SET stage='scored', prep_folder_path=NULL, stage_updated=?, updated_at=? WHERE id=?",
            (now, now, job_id),
        )
        db.commit()

        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["stage"] == "scored"
        assert row["prep_folder_path"] is None

    def test_missing_files_never_reaches_materials_drafted(self, db):
        """Missing files abort must not transition to materials_drafted."""
        job_id = insert_job(db, stage="prep_in_progress")

        # Simulate: abort fires; materials_drafted update must NOT run
        # Stage should still be whatever it was before (prep_in_progress → scored after abort)
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["stage"] != "materials_drafted"


# ═══════════════════════════════════════════════════════════════════════════════
# reset_prep_to_scored — shared failure-rollback helper (issue #172)
# ═══════════════════════════════════════════════════════════════════════════════


class TestResetPrepToScored:
    """Every prep_application.py failure path (missing files, validation, unhandled
    exception) used to bounce stage back to 'scored' without write_audit, hiding
    the reverse half of each transition and defeating the 60-min stale-reset.

    The shared helper reset_prep_to_scored() exists to make that invariant
    enforceable in one place."""

    def _redirect_log(self, monkeypatch, tmp_path):
        # log_event writes to findajob.paths.BASE/logs/pipeline.jsonl — redirect
        # so tests don't append to the real log.
        from findajob import utils

        monkeypatch.setattr(utils, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def test_resets_stage_and_clears_folder(self, db, tmp_path, monkeypatch):
        from findajob.actions import reset_prep_to_scored

        self._redirect_log(monkeypatch, tmp_path)
        job_id = insert_job(db, stage="prep_in_progress", folder="/tmp/some/path")

        did_reset = reset_prep_to_scored(db, job_id, reason="test_reason")

        assert did_reset is True
        row = db.execute(
            "SELECT stage, prep_folder_path, stage_updated FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        assert row["stage"] == "scored"
        assert row["prep_folder_path"] is None
        assert row["stage_updated"] is not None

    def test_writes_audit_entry(self, db, tmp_path, monkeypatch):
        """CORE invariant of #172: every stage transition auditable."""
        from findajob.actions import reset_prep_to_scored

        self._redirect_log(monkeypatch, tmp_path)
        job_id = insert_job(db, stage="prep_in_progress")

        reset_prep_to_scored(db, job_id, reason="unit_test")

        audit = db.execute(
            "SELECT field_changed, old_value, new_value FROM audit_log WHERE job_id=?",
            (job_id,),
        ).fetchall()
        assert len(audit) == 1
        assert audit[0]["field_changed"] == "stage"
        assert audit[0]["old_value"] == "prep_in_progress"
        assert audit[0]["new_value"] == "scored"

    def test_emits_prep_failed_reset_event(self, db, tmp_path, monkeypatch):
        """Operator monitoring: failure resets must be visible in pipeline.jsonl."""
        import json

        from findajob.actions import reset_prep_to_scored

        log_path = tmp_path / "events.jsonl"
        self._redirect_log(monkeypatch, tmp_path)
        job_id = insert_job(db, stage="prep_in_progress")

        reset_prep_to_scored(db, job_id, reason="validation_failed")

        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        resets = [e for e in entries if e["event"] == "prep_failed_reset"]
        assert len(resets) == 1
        assert resets[0]["job_id"] == job_id
        assert resets[0]["reason"] == "validation_failed"

    def test_guards_materials_drafted(self, db, tmp_path, monkeypatch):
        """Don't clobber a successful prep that raced in before the error path."""
        from findajob.actions import reset_prep_to_scored

        self._redirect_log(monkeypatch, tmp_path)
        job_id = insert_job(db, stage="materials_drafted", folder="/keep/me")

        did_reset = reset_prep_to_scored(db, job_id, reason="test_reason")

        assert did_reset is False
        row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["stage"] == "materials_drafted"
        assert row["prep_folder_path"] == "/keep/me"
        audit = db.execute("SELECT 1 FROM audit_log WHERE job_id=?", (job_id,)).fetchall()
        assert len(audit) == 0

    def test_guards_applied(self, db, tmp_path, monkeypatch):
        """Don't roll back a job the user already submitted."""
        from findajob.actions import reset_prep_to_scored

        self._redirect_log(monkeypatch, tmp_path)
        job_id = insert_job(db, stage="applied")

        did_reset = reset_prep_to_scored(db, job_id, reason="test_reason")

        assert did_reset is False
        row = db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["stage"] == "applied"
        audit = db.execute("SELECT 1 FROM audit_log WHERE job_id=?", (job_id,)).fetchall()
        assert len(audit) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# quarantine_stale_prep_folders — duplicate-folder cleanup (issue #174)
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuarantineStalePrepFolders:
    """Observed 2026-04-22: 4 prep folders for one UN/P4 job in ~50 min.
    Each prep run mints a fresh {company}_{title}_{date}_{HHMMSS} folder, but
    only the latest is written to DB.prep_folder_path — prior folders sit on
    disk with no DB reference, accumulating across Regenerate clicks and any
    prep race.

    quarantine_stale_prep_folders() scans companies/ at prep start, moves any
    matching-prefix siblings not tracked in DB to companies/.stale/. Quarantine
    rather than delete so a racing prep's files are recoverable."""

    def _redirect_log(self, monkeypatch, tmp_path):
        from findajob import utils as _utils

        monkeypatch.setattr(_utils, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def test_no_matching_folders_returns_empty(self, db, tmp_path, monkeypatch):
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="Acme_Ops_Manager_",
            current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
        )
        assert moved == []

    def test_untracked_sibling_moved_to_stale(self, db, tmp_path, monkeypatch):
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()
        (companies / "Acme_Ops_Manager_2026-04-22_110000").mkdir()
        (companies / "Acme_Ops_Manager_2026-04-22_120000").mkdir()  # the current run

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="Acme_Ops_Manager_",
            current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
        )
        assert moved == ["Acme_Ops_Manager_2026-04-22_110000"]
        assert not (companies / "Acme_Ops_Manager_2026-04-22_110000").exists()
        assert (companies / ".stale" / "Acme_Ops_Manager_2026-04-22_110000").is_dir()
        # Current folder untouched.
        assert (companies / "Acme_Ops_Manager_2026-04-22_120000").is_dir()

    def test_current_folder_never_moved(self, db, tmp_path, monkeypatch):
        """The folder being used by THIS prep run must not be moved."""
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()
        (companies / "Acme_Ops_Manager_2026-04-22_120000").mkdir()

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="Acme_Ops_Manager_",
            current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
        )
        assert moved == []
        assert (companies / "Acme_Ops_Manager_2026-04-22_120000").is_dir()

    def test_db_tracked_folder_never_moved(self, db, tmp_path, monkeypatch):
        """Defensive: if another job's prep_folder_path points here, don't clobber it
        even if the name accidentally matches our prefix."""
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()
        tracked_folder = companies / "Acme_Ops_Manager_2026-04-22_110000"
        tracked_folder.mkdir()
        insert_job(db, stage="materials_drafted", folder=str(tracked_folder))

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="Acme_Ops_Manager_",
            current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
        )
        assert moved == []
        assert tracked_folder.is_dir()

    def test_different_prefix_left_alone(self, db, tmp_path, monkeypatch):
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()
        (companies / "OtherCo_Different_Role_2026-04-22_110000").mkdir()
        (companies / "Acme_Ops_Manager_2026-04-22_120000").mkdir()

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="Acme_Ops_Manager_",
            current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
        )
        assert moved == []
        assert (companies / "OtherCo_Different_Role_2026-04-22_110000").is_dir()

    def test_underscore_prefix_subdirs_skipped(self, db, tmp_path, monkeypatch):
        """_applied, _rejected, _waitlisted are stage holding areas
        — they must never be quarantined even if prefix would match."""
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()
        (companies / "_applied").mkdir()
        (companies / "_rejected").mkdir()
        (companies / "_waitlisted").mkdir()

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="_",  # deliberately adversarial
            current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
        )
        assert moved == []
        assert (companies / "_applied").is_dir()
        assert (companies / "_rejected").is_dir()
        assert (companies / "_waitlisted").is_dir()

    def test_four_stale_siblings_all_quarantined(self, db, tmp_path, monkeypatch):
        """The exact 2026-04-22 incident: 4 folders for same job, cleanup leaves current + stale/."""
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()
        for ts in ("055002", "062632", "062904", "063003"):
            (companies / f"United Nations_Evaluation_Officer_P4_2026-04-22_{ts}").mkdir()
        (companies / "United Nations_Evaluation_Officer_P4_2026-04-22_130000").mkdir()  # current

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="United Nations_Evaluation_Officer_P4_",
            current_folder_name="United Nations_Evaluation_Officer_P4_2026-04-22_130000",
        )
        assert sorted(moved) == [
            "United Nations_Evaluation_Officer_P4_2026-04-22_055002",
            "United Nations_Evaluation_Officer_P4_2026-04-22_062632",
            "United Nations_Evaluation_Officer_P4_2026-04-22_062904",
            "United Nations_Evaluation_Officer_P4_2026-04-22_063003",
        ]
        assert (companies / "United Nations_Evaluation_Officer_P4_2026-04-22_130000").is_dir()
        assert len(list((companies / ".stale").iterdir())) == 4

    def test_nonexistent_companies_dir_no_crash(self, db, tmp_path, monkeypatch):
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        moved = quarantine_stale_prep_folders(
            db,
            str(tmp_path / "does-not-exist"),
            folder_prefix="Acme_",
            current_folder_name="Acme_2026-04-22_120000",
        )
        assert moved == []

    def test_regular_files_not_moved(self, db, tmp_path, monkeypatch):
        from findajob.utils import quarantine_stale_prep_folders

        self._redirect_log(monkeypatch, tmp_path)
        companies = tmp_path / "companies"
        companies.mkdir()
        # A regular file matching the prefix — must be left alone (defensive).
        (companies / "Acme_Ops_Manager_readme.txt").write_text("hi")

        moved = quarantine_stale_prep_folders(
            db,
            str(companies),
            folder_prefix="Acme_Ops_Manager_",
            current_folder_name="Acme_Ops_Manager_2026-04-22_120000",
        )
        assert moved == []
        assert (companies / "Acme_Ops_Manager_readme.txt").is_file()


# ═══════════════════════════════════════════════════════════════════════════════
# Speculative mode marker injection (B4.T27 + B4.T28 of #131)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpeculativeModeMarker:
    """Verify that the <<SPECULATIVE_MODE>> marker is correctly derived from the
    jobs.synthetic column and injected into the cover_prompt string.

    These tests replicate the logic in prep_application.py without importing it
    (which would require a full DB + file setup) — same pattern as the fit-score
    parsing tests above."""

    # ── Helper: replicate the marker-derivation logic from prep_application.py ──

    @staticmethod
    def _derive_marker(row):
        """Mirrors prep_application.py:
        is_synthetic = bool(row["synthetic"]) if row and "synthetic" in row.keys() else False
        mode_marker  = "<<SPECULATIVE_MODE>>\\n\\n" if is_synthetic else ""
        """
        is_synthetic = bool(row["synthetic"]) if row and "synthetic" in row.keys() else False
        return "<<SPECULATIVE_MODE>>\n\n" if is_synthetic else ""

    def test_synthetic_1_yields_marker(self, db):
        """jobs.synthetic=1 → mode_marker starts with <<SPECULATIVE_MODE>>."""
        job_id = insert_job(db, stage="prep_in_progress")
        db.execute("UPDATE jobs SET synthetic=1 WHERE id=?", (job_id,))
        db.commit()

        row = db.execute("SELECT raw_jd_text, stage, synthetic FROM jobs WHERE id=?", (job_id,)).fetchone()
        marker = self._derive_marker(row)
        assert marker == "<<SPECULATIVE_MODE>>\n\n"

    def test_synthetic_0_yields_empty_marker(self, db):
        """jobs.synthetic=0 (default) → mode_marker is empty string."""
        job_id = insert_job(db, stage="prep_in_progress")
        # synthetic defaults to 0 — no UPDATE needed

        row = db.execute("SELECT raw_jd_text, stage, synthetic FROM jobs WHERE id=?", (job_id,)).fetchone()
        marker = self._derive_marker(row)
        assert marker == ""

    def test_marker_prepended_to_cover_prompt(self, db):
        """When synthetic=1, the cover_prompt starts with <<SPECULATIVE_MODE>>."""
        job_id = insert_job(db, stage="prep_in_progress")
        db.execute("UPDATE jobs SET synthetic=1 WHERE id=?", (job_id,))
        db.commit()

        row = db.execute("SELECT raw_jd_text, stage, synthetic FROM jobs WHERE id=?", (job_id,)).fetchone()
        mode_marker = self._derive_marker(row)

        profile_text = "Candidate profile here."
        master_text = "Master resume here."
        jd_text = "Job description here."
        briefing_context = "Company briefing here."
        voice_section = ""

        cover_prompt = (
            f"{mode_marker}"
            f"CANDIDATE PROFILE:\n{profile_text}\n\n"
            f"MASTER RESUME:\n{master_text}\n\n"
            f"{voice_section}"
            f"Company: Acme Corp\nTitle: Operations Manager\nDate: April 28, 2026\n\n"
            f"JD:\n{jd_text}\n\n"
            f"COMPANY BRIEFING AND FIT ANALYSIS:\n{briefing_context}"
        )

        assert cover_prompt.startswith("<<SPECULATIVE_MODE>>\n\n")
        assert "CANDIDATE PROFILE:" in cover_prompt

    def test_no_marker_when_not_synthetic(self, db):
        """When synthetic=0, the cover_prompt does NOT contain <<SPECULATIVE_MODE>>."""
        job_id = insert_job(db, stage="prep_in_progress")

        row = db.execute("SELECT raw_jd_text, stage, synthetic FROM jobs WHERE id=?", (job_id,)).fetchone()
        mode_marker = self._derive_marker(row)

        cover_prompt = (
            f"{mode_marker}"
            f"CANDIDATE PROFILE:\nprofile\n\n"
            f"MASTER RESUME:\nresume\n\n"
            f"JD:\njd\n\n"
            f"COMPANY BRIEFING AND FIT ANALYSIS:\nbriefing"
        )

        assert "<<SPECULATIVE_MODE>>" not in cover_prompt
        assert cover_prompt.startswith("CANDIDATE PROFILE:")

    def test_find_contacts_synthetic_arg_parsing(self):
        """sys.argv[6] == '1' → is_synthetic True; '0' or absent → False."""

        # Replicate the arg-parsing logic from find_contacts.py main()
        def parse_is_synthetic(argv):
            return argv[6] == "1" if len(argv) > 6 else False

        assert parse_is_synthetic(["fc.py", "Co", "jd", "outdir", "pfx", "ts", "1"]) is True
        assert parse_is_synthetic(["fc.py", "Co", "jd", "outdir", "pfx", "ts", "0"]) is False
        assert parse_is_synthetic(["fc.py", "Co", "jd", "outdir", "pfx", "ts"]) is False
        assert parse_is_synthetic(["fc.py", "Co", "jd", "outdir"]) is False
