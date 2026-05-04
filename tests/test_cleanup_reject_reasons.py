"""Tests for scripts/cleanup_reject_reasons.py — the one-shot data cleanup
that normalizes jobs.reject_reason to the canonical vocabulary (#445).

Each test patches DB_PATH to a tmp_path SQLite file and patches
load_reject_reasons() to return a known canonical set, then asserts the
script's effect on a pre-state DB seeded with rows from each bucket
(canonical, case-dup, pipeline-internal markers, stale operator vocab,
unmapped fallback).

audit_log changed_at uses the SQL `datetime('now')` default (space-
separated naive UTC); jobs.* timestamp columns aren't load-bearing here so
they're left blank. (Memory: feedback_audit_log_timestamp_format.md)
"""

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cleanup_reject_reasons  # noqa: E402

from findajob import config_loader  # noqa: E402

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'rejected',
    reject_reason TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
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

CANONICAL = (
    "Skills Mismatch",
    "Stale/Closed",
    "Already Applied",
    "Low Fit Score",
    "Company passed",
    "Other",
)


def _insert(conn, reject_reason):
    """Insert a job row with the given reject_reason. Returns job_id."""
    job_id = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, reject_reason) VALUES (?, ?, ?, ?, ?, ?)",
        (
            job_id,
            f"fp_{job_id}",
            f"https://example.com/{job_id}",
            "Some Title",
            "Some Company",
            reject_reason,
        ),
    )
    conn.commit()
    return job_id


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Build a tmp SQLite DB seeded with one row per bucket; patch DB_PATH +
    canonical-reasons cache to match CANONICAL above."""
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)

    rows = {
        # canonical — must remain unchanged
        "canonical_skills": _insert(conn, "Skills Mismatch"),
        "canonical_other": _insert(conn, "Other"),
        # case-dup of "Blank company" — both map to "Other"
        "blank_lower": _insert(conn, "Blank company"),
        "blank_upper": _insert(conn, "Blank Company"),
        # pre-#429 free-text vocabulary — map to "Skills Mismatch"
        "wrong_niche": _insert(conn, "Wrong Niche"),
        "too_software": _insert(conn, "Too Software/Systems"),
        "too_facilities": _insert(conn, "Too Facilities/MEP"),
        "too_manufacturing": _insert(conn, "Too Manufacturing/Test"),
        # pipeline-internal markers from deleted code — map to "Other"
        "duplicate_entry": _insert(conn, "duplicate_entry"),
        "ingest_noise": _insert(conn, "Ingest noise (aggregator_company)"),
        "stuck_discovered": _insert(conn, "Stuck in discovered"),
        "not_real_job": _insert(conn, "Not a Real Job"),
        # unmapped non-canonical — falls back to "Other" with warning
        "unknown_garbage": _insert(conn, "Some Future Garbage Value"),
        # blank — should be ignored (NULL/empty already filtered out)
        "blank_reason": _insert(conn, ""),
    }
    conn.close()

    monkeypatch.setattr(cleanup_reject_reasons, "DB_PATH", db_path)
    config_loader._reset_cache()
    monkeypatch.setattr(
        config_loader,
        "_DEFAULT_REJECT_REASONS",
        CANONICAL,
    )
    # Force the loader into the default-fallback path by pointing the YAML
    # at a non-existent location.
    monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", tmp_path / "missing.yaml")

    return db_path, rows


def _read_reasons(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = {r["id"]: r["reject_reason"] for r in conn.execute("SELECT id, reject_reason FROM jobs")}
    conn.close()
    return rows


def _read_audit(db_path, *, changed_by=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if changed_by is None:
        rows = conn.execute("SELECT * FROM audit_log").fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log WHERE changed_by=?", (changed_by,)).fetchall()
    conn.close()
    return rows


class TestCleanup:
    def test_canonical_rows_unchanged(self, seeded_db):
        db_path, rows = seeded_db
        rc = cleanup_reject_reasons.cleanup()
        assert rc == 0

        post = _read_reasons(db_path)
        assert post[rows["canonical_skills"]] == "Skills Mismatch"
        assert post[rows["canonical_other"]] == "Other"

    def test_blank_company_case_dup_to_other(self, seeded_db):
        db_path, rows = seeded_db
        cleanup_reject_reasons.cleanup()

        post = _read_reasons(db_path)
        assert post[rows["blank_lower"]] == "Other"
        assert post[rows["blank_upper"]] == "Other"

    def test_stale_operator_vocab_to_skills_mismatch(self, seeded_db):
        db_path, rows = seeded_db
        cleanup_reject_reasons.cleanup()

        post = _read_reasons(db_path)
        assert post[rows["wrong_niche"]] == "Skills Mismatch"
        assert post[rows["too_software"]] == "Skills Mismatch"
        assert post[rows["too_facilities"]] == "Skills Mismatch"
        assert post[rows["too_manufacturing"]] == "Skills Mismatch"

    def test_pipeline_markers_to_other(self, seeded_db):
        db_path, rows = seeded_db
        cleanup_reject_reasons.cleanup()

        post = _read_reasons(db_path)
        assert post[rows["duplicate_entry"]] == "Other"
        assert post[rows["ingest_noise"]] == "Other"
        assert post[rows["stuck_discovered"]] == "Other"
        assert post[rows["not_real_job"]] == "Other"

    def test_unmapped_non_canonical_falls_back_to_other(self, seeded_db):
        db_path, rows = seeded_db
        cleanup_reject_reasons.cleanup()

        post = _read_reasons(db_path)
        assert post[rows["unknown_garbage"]] == "Other"

    def test_blank_reject_reason_untouched(self, seeded_db):
        db_path, rows = seeded_db
        cleanup_reject_reasons.cleanup()

        post = _read_reasons(db_path)
        assert post[rows["blank_reason"]] == ""

    def test_audit_log_records_changes(self, seeded_db):
        db_path, rows = seeded_db
        cleanup_reject_reasons.cleanup()

        audit = _read_audit(db_path, changed_by="reject_reason_cleanup_445")
        # Every non-canonical, non-blank row got an audit entry
        assert len(audit) == 11

        # Spot-check one mapping
        wrong_niche_audit = [r for r in audit if r["job_id"] == rows["wrong_niche"]]
        assert len(wrong_niche_audit) == 1
        assert wrong_niche_audit[0]["field_changed"] == "reject_reason"
        assert wrong_niche_audit[0]["old_value"] == "Wrong Niche"
        assert wrong_niche_audit[0]["new_value"] == "Skills Mismatch"

    def test_audit_changed_by_is_specific_marker(self, seeded_db):
        db_path, _rows = seeded_db
        cleanup_reject_reasons.cleanup()

        all_audit = _read_audit(db_path)
        markers = {r["changed_by"] for r in all_audit}
        # All entries from this run should carry the cleanup_445 marker
        assert markers == {"reject_reason_cleanup_445"}

    def test_idempotent(self, seeded_db):
        db_path, _rows = seeded_db
        # First run: changes everything
        cleanup_reject_reasons.cleanup()
        first_audit_count = len(_read_audit(db_path))
        first_reasons = _read_reasons(db_path)

        # Second run: no-op (all rows now canonical)
        config_loader._reset_cache()
        cleanup_reject_reasons.cleanup()
        second_audit_count = len(_read_audit(db_path))
        second_reasons = _read_reasons(db_path)

        assert first_audit_count == second_audit_count
        assert first_reasons == second_reasons

    def test_dry_run_changes_nothing(self, seeded_db):
        db_path, rows = seeded_db
        pre = _read_reasons(db_path)

        cleanup_reject_reasons.cleanup(dry_run=True)

        post = _read_reasons(db_path)
        assert pre == post
        # No audit entries written
        assert len(_read_audit(db_path, changed_by="reject_reason_cleanup_445")) == 0
