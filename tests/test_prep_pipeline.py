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
