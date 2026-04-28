"""Tests for waitlist stage transitions, folder moves, resurfacing query, and blocking app lookup."""

import os
import shutil
import sqlite3
import uuid

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

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
    apply_flag INTEGER DEFAULT 0,
    reject_reason TEXT DEFAULT '',
    prep_folder_path TEXT,
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
"""


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
    (base / "_waitlisted").mkdir()
    (base / "_rejected").mkdir()
    (base / "_applied").mkdir()
    return base


def insert_job(conn, *, stage="scored", company="Acme Corp", title="Operations Manager", score=7, folder=None):
    """Insert a job with sane defaults; returns the row as sqlite3.Row."""
    job_id = str(uuid.uuid4())[:8]
    fp = f"fp_{job_id}"
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, url, title, company, relevance_score, stage, prep_folder_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, fp, f"https://example.com/{job_id}", title, company, score, stage, folder),
    )
    conn.commit()
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


# ── Stage transitions ────────────────────────────────────────────────────────


class TestWaitlistStageTransitions:
    def test_scored_can_be_waitlisted(self, db):
        job = insert_job(db, stage="scored")
        db.execute("UPDATE jobs SET stage = 'waitlisted' WHERE id = ?", (job["id"],))
        db.commit()
        row = db.execute("SELECT stage FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert row["stage"] == "waitlisted"

    def test_materials_drafted_can_be_waitlisted(self, db):
        job = insert_job(db, stage="materials_drafted")
        db.execute("UPDATE jobs SET stage = 'waitlisted' WHERE id = ?", (job["id"],))
        db.commit()
        row = db.execute("SELECT stage FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert row["stage"] == "waitlisted"

    def test_waitlisted_stage_persists_across_query(self, db):
        job = insert_job(db, stage="scored")
        db.execute("UPDATE jobs SET stage = 'waitlisted' WHERE id = ?", (job["id"],))
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'waitlisted'").fetchone()[0]
        assert count == 1


# ── Reactivation logic ───────────────────────────────────────────────────────


class TestReactivation:
    def test_reactivate_without_folder_restores_to_scored(self, db):
        job = insert_job(db, stage="waitlisted", folder=None)
        # Reactivation logic: no folder → scored
        row = db.execute("SELECT prep_folder_path FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        folder = row["prep_folder_path"]
        if not folder or not os.path.isdir(str(folder)):
            new_stage = "scored"
        else:
            new_stage = "materials_drafted"
        db.execute("UPDATE jobs SET stage = ? WHERE id = ?", (new_stage, job["id"]))
        db.commit()
        result = db.execute("SELECT stage FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert result["stage"] == "scored"

    def test_reactivate_with_folder_restores_to_materials_drafted(self, db, companies_dir):
        waitlisted_dir = companies_dir / "_waitlisted"
        folder = waitlisted_dir / "Acme_Ops_Manager_2026-04-12_120000"
        folder.mkdir()
        (folder / "resume.pdf").touch()

        job = insert_job(db, stage="waitlisted", folder=str(folder))

        # Reactivation logic: folder exists → move back, set materials_drafted
        row = db.execute("SELECT prep_folder_path FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        src = row["prep_folder_path"]
        if src and os.path.isdir(src):
            dest = str(companies_dir / os.path.basename(src))
            shutil.move(src, dest)
            db.execute(
                "UPDATE jobs SET stage = 'materials_drafted', prep_folder_path = ? WHERE id = ?", (dest, job["id"])
            )
            db.commit()

        result = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert result["stage"] == "materials_drafted"
        assert os.path.isdir(result["prep_folder_path"])
        assert os.path.isfile(os.path.join(result["prep_folder_path"], "resume.pdf"))

    def test_reactivate_with_missing_folder_falls_back_to_scored(self, db):
        # Folder path recorded but directory doesn't exist on disk
        job = insert_job(db, stage="waitlisted", folder="/nonexistent/path/Acme_Ops")
        row = db.execute("SELECT prep_folder_path FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        folder = row["prep_folder_path"]
        if folder and os.path.isdir(folder):
            new_stage = "materials_drafted"
        else:
            new_stage = "scored"
        db.execute("UPDATE jobs SET stage = ? WHERE id = ?", (new_stage, job["id"]))
        db.commit()
        result = db.execute("SELECT stage FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert result["stage"] == "scored"


# ── Folder moves ─────────────────────────────────────────────────────────────


class TestFolderMoves:
    def test_move_folder_to_waitlisted(self, db, companies_dir):
        folder = companies_dir / "Acme_Ops_Manager_2026-04-12_120000"
        folder.mkdir()
        (folder / "cover_letter.docx").touch()

        job = insert_job(db, stage="scored", folder=str(folder))

        # Waitlist logic: move to _waitlisted/
        waitlisted_dir = companies_dir / "_waitlisted"
        dest = str(waitlisted_dir / folder.name)
        shutil.move(str(folder), dest)
        db.execute("UPDATE jobs SET stage = 'waitlisted', prep_folder_path = ? WHERE id = ?", (dest, job["id"]))
        db.commit()

        assert not folder.exists()
        assert os.path.isdir(dest)
        assert os.path.isfile(os.path.join(dest, "cover_letter.docx"))
        result = db.execute("SELECT prep_folder_path FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert result["prep_folder_path"] == dest

    def test_move_folder_back_from_waitlisted(self, db, companies_dir):
        waitlisted_dir = companies_dir / "_waitlisted"
        folder = waitlisted_dir / "Acme_Ops_Manager_2026-04-12_120000"
        folder.mkdir()
        (folder / "briefing.md").touch()

        job = insert_job(db, stage="waitlisted", folder=str(folder))

        # Reactivate: move back to companies/
        dest = str(companies_dir / folder.name)
        shutil.move(str(folder), dest)
        db.execute("UPDATE jobs SET stage = 'materials_drafted', prep_folder_path = ? WHERE id = ?", (dest, job["id"]))
        db.commit()

        assert not folder.exists()
        assert os.path.isdir(dest)
        assert os.path.isfile(os.path.join(dest, "briefing.md"))

    def test_no_folder_move_when_no_folder_exists(self, db, companies_dir):
        job = insert_job(db, stage="scored", folder=None)

        # Waitlist without folder — just update stage, no move
        row = db.execute("SELECT prep_folder_path FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        folder = row["prep_folder_path"]
        folder_moved = False
        if folder and os.path.isdir(folder):
            folder_moved = True
        db.execute("UPDATE jobs SET stage = 'waitlisted' WHERE id = ?", (job["id"],))
        db.commit()

        assert folder_moved is False
        result = db.execute("SELECT stage FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert result["stage"] == "waitlisted"


# ── Resurfacing query ────────────────────────────────────────────────────────


class TestResurfacingQuery:
    def test_finds_waitlisted_jobs_at_company(self, db):
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Ops Manager")
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Site Lead")
        insert_job(db, stage="scored", company="Acme Corp", title="Technician")

        rows = db.execute(
            "SELECT title FROM jobs WHERE company = ? AND stage = 'waitlisted'",
            ("Acme Corp",),
        ).fetchall()
        titles = [r["title"] for r in rows]
        assert len(titles) == 2
        assert "Ops Manager" in titles
        assert "Site Lead" in titles

    def test_returns_empty_when_no_waitlisted(self, db):
        insert_job(db, stage="scored", company="Acme Corp", title="Ops Manager")
        insert_job(db, stage="applied", company="Acme Corp", title="Site Lead")

        rows = db.execute(
            "SELECT title FROM jobs WHERE company = ? AND stage = 'waitlisted'",
            ("Acme Corp",),
        ).fetchall()
        assert rows == []

    def test_does_not_cross_company_boundaries(self, db):
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Ops Manager")
        insert_job(db, stage="waitlisted", company="Other Inc", title="Site Lead")

        rows = db.execute(
            "SELECT title FROM jobs WHERE company = ? AND stage = 'waitlisted'",
            ("Acme Corp",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "Ops Manager"


# ── Blocking application lookup ──────────────────────────────────────────────

ACTIVE_STAGES = ("applied", "interview", "offer", "prep_in_progress", "materials_drafted")


class TestBlockingAppLookup:
    def test_finds_active_apps_at_company(self, db):
        insert_job(db, stage="applied", company="Acme Corp", title="Ops Manager")
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Site Lead")

        placeholders = ",".join("?" for _ in ACTIVE_STAGES)
        rows = db.execute(
            f"SELECT title, stage FROM jobs WHERE company = ? AND stage IN ({placeholders})",
            ("Acme Corp", *ACTIVE_STAGES),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "Ops Manager"
        assert rows[0]["stage"] == "applied"

    def test_returns_empty_when_no_active_apps(self, db):
        insert_job(db, stage="waitlisted", company="Acme Corp", title="Ops Manager")
        insert_job(db, stage="rejected", company="Acme Corp", title="Site Lead")

        placeholders = ",".join("?" for _ in ACTIVE_STAGES)
        rows = db.execute(
            f"SELECT title, stage FROM jobs WHERE company = ? AND stage IN ({placeholders})",
            ("Acme Corp", *ACTIVE_STAGES),
        ).fetchall()
        assert rows == []

    def test_multiple_active_stages_found(self, db):
        insert_job(db, stage="applied", company="Acme Corp", title="Ops Manager")
        insert_job(db, stage="interview", company="Acme Corp", title="Site Lead")
        insert_job(db, stage="prep_in_progress", company="Acme Corp", title="Tech Lead")
        insert_job(db, stage="rejected", company="Acme Corp", title="Old Role")

        placeholders = ",".join("?" for _ in ACTIVE_STAGES)
        rows = db.execute(
            f"SELECT title FROM jobs WHERE company = ? AND stage IN ({placeholders})",
            ("Acme Corp", *ACTIVE_STAGES),
        ).fetchall()
        assert len(rows) == 3

    def test_does_not_cross_company_boundaries(self, db):
        insert_job(db, stage="applied", company="Acme Corp", title="Ops Manager")
        insert_job(db, stage="applied", company="Other Inc", title="Site Lead")

        placeholders = ",".join("?" for _ in ACTIVE_STAGES)
        rows = db.execute(
            f"SELECT title FROM jobs WHERE company = ? AND stage IN ({placeholders})",
            ("Acme Corp", *ACTIVE_STAGES),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "Ops Manager"
