"""Tests for #495: subprocess failures in prep_application surface and roll back stage."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from findajob.prep.orchestrator import _handle_prep_subprocess_failure

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


@pytest.fixture()
def isolate_event_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_path = tmp_path / "pipeline.jsonl"
    monkeypatch.setattr("findajob.audit.LOG_PATH", str(log_path))
    return log_path


@pytest.fixture(autouse=True)
def stub_quick_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "findajob.prep.orchestrator.quick_notify",
        lambda _msg: None,
    )


def _insert_prep_in_progress(conn: sqlite3.Connection, job_id: str = "job-1") -> None:
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, stage) VALUES (?, ?, ?, ?, ?, 'prep_in_progress')",
        (job_id, f"fp-{job_id}", "https://x.test/job", "Engineer", "Acme"),
    )
    conn.commit()


def test_handler_rolls_stage_back_to_scored(db, tmp_path, isolate_event_log):
    _insert_prep_in_progress(db)
    outdir = tmp_path / "Acme_Eng_2026-05-10_120000"
    outdir.mkdir()
    exc = subprocess.CalledProcessError(
        returncode=1,
        cmd=["/usr/bin/pandoc", "-o", "out.docx", "in.md"],
        stderr=b"pandoc: input file not found",
    )

    _handle_prep_subprocess_failure(db, "job-1", "Acme", "Engineer", str(outdir), exc)

    stage = db.execute("SELECT stage FROM jobs WHERE id='job-1'").fetchone()["stage"]
    assert stage == "scored"


def test_handler_writes_audit_log_row(db, tmp_path, isolate_event_log):
    _insert_prep_in_progress(db)
    outdir = tmp_path / "out"
    outdir.mkdir()
    exc = subprocess.CalledProcessError(returncode=1, cmd=["/usr/bin/pandoc"], stderr=b"")

    _handle_prep_subprocess_failure(db, "job-1", "Acme", "Engineer", str(outdir), exc)

    rows = db.execute(
        "SELECT old_value, new_value FROM audit_log WHERE job_id='job-1' AND field_changed='stage'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["old_value"] == "prep_in_progress"
    assert rows[0]["new_value"] == "scored"


def test_handler_writes_failed_subprocess_sentinel(db, tmp_path, isolate_event_log):
    _insert_prep_in_progress(db)
    outdir = tmp_path / "out"
    outdir.mkdir()
    exc = subprocess.CalledProcessError(
        returncode=2,
        cmd=["/usr/bin/pandoc", "-o", "resume.docx"],
        stderr=b"some error trail",
    )

    _handle_prep_subprocess_failure(db, "job-1", "Acme", "Engineer", str(outdir), exc)

    sentinel = outdir / ".failed_subprocess"
    assert sentinel.exists(), "sentinel file must exist after handler"
    text = sentinel.read_text()
    assert "/usr/bin/pandoc" in text
    assert "returncode: 2" in text
    assert "some error trail" in text


def test_handler_emits_pipeline_event(db, tmp_path, isolate_event_log):
    _insert_prep_in_progress(db)
    outdir = tmp_path / "out"
    outdir.mkdir()
    exc = subprocess.CalledProcessError(returncode=1, cmd=["/usr/bin/pandoc"], stderr=b"")

    _handle_prep_subprocess_failure(db, "job-1", "Acme", "Engineer", str(outdir), exc)

    events = [json.loads(line) for line in isolate_event_log.read_text().splitlines() if line.strip()]
    failed_events = [e for e in events if e["event"] == "prep_subprocess_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["company"] == "Acme"
    assert failed_events[0]["job_id"] == "job-1"
    assert failed_events[0]["returncode"] == 1


def test_handler_does_not_advance_to_materials_drafted(db, tmp_path, isolate_event_log):
    _insert_prep_in_progress(db)
    outdir = tmp_path / "out"
    outdir.mkdir()
    exc = subprocess.CalledProcessError(returncode=1, cmd=["/usr/bin/pandoc"], stderr=b"")

    _handle_prep_subprocess_failure(db, "job-1", "Acme", "Engineer", str(outdir), exc)

    rows = db.execute("SELECT new_value FROM audit_log WHERE job_id='job-1' AND field_changed='stage'").fetchall()
    new_values = [r["new_value"] for r in rows]
    assert "materials_drafted" not in new_values


def test_handler_sentinel_best_effort_when_outdir_missing(db, tmp_path, isolate_event_log):
    _insert_prep_in_progress(db)
    nonexistent = tmp_path / "never_created"
    exc = subprocess.CalledProcessError(returncode=1, cmd=["/usr/bin/pandoc"], stderr=b"")

    # Must not raise even though outdir doesn't exist
    _handle_prep_subprocess_failure(db, "job-1", "Acme", "Engineer", str(nonexistent), exc)

    stage = db.execute("SELECT stage FROM jobs WHERE id='job-1'").fetchone()["stage"]
    assert stage == "scored"
    assert not (nonexistent / ".failed_subprocess").exists()


def test_subprocess_check_true_at_must_succeed_sites():
    # Mechanical guard: the four must-succeed subprocess.run sites
    # (3× pandoc, 1× find_contacts) must use check=True. Without this,
    # silent failures re-emerge — the regression #495 was filed against.
    src = (Path(os.path.dirname(__file__)).parent / "src/findajob/prep/orchestrator.py").read_text()
    # The advisory curl/pandoc-fallback subprocess at L141-149 + the
    # validate_resume.py informational call are excluded by their
    # surrounding try/except (they don't go through this code path).
    import re

    pattern = re.compile(r"check=True,\s*\n\s*capture_output=True,")
    matches = pattern.findall(src)
    assert len(matches) == 4, f"expected 4 must-succeed sites with check=True, found {len(matches)}"
