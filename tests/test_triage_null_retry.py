"""Tests for null-score manual_review retry logic in triage.py.

score_null_manual_review_rows() re-scores rows that landed in
manual_review with relevance_score=NULL (scorer timeout/failure).
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

MINIMAL_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    location TEXT DEFAULT '',
    raw_jd_text TEXT,
    relevance_score INTEGER,
    interview_likelihood INTEGER,
    strengths_alignment TEXT DEFAULT '',
    industry_sector TEXT DEFAULT '',
    comp_estimate TEXT DEFAULT '',
    ai_notes TEXT DEFAULT '',
    score_status TEXT DEFAULT '',
    score_flag_reason TEXT DEFAULT '',
    remote_status TEXT DEFAULT 'Unknown',
    stage TEXT DEFAULT 'enriched',
    stage_updated TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'active',
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
"""

GOOD_SCORE = {
    "relevance_score": 8,
    "interview_likelihood": 7,
    "strengths_alignment": "Strong",
    "industry_sector": "Tech",
    "comp_estimate": "$150k",
    "ai_notes": "Good fit",
    "score_status": "scored",
    "score_flag_reason": "",
    "remote_status": "Remote",
}


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_SCHEMA)
    return conn


def _insert_job(conn, job_id, stage, relevance_score=None, days_old=0):
    stage_updated = f"datetime('now', '-{days_old} days')" if days_old else "datetime('now')"
    conn.execute(
        f"""INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, stage_updated)
            VALUES (?, ?, 't', 'Co', ?, ?, ({stage_updated}))""",
        (job_id, job_id, stage, relevance_score),
    )
    conn.commit()


def _load_triage():

    spec = importlib.util.spec_from_file_location("triage", SCRIPTS_DIR / "triage.py")
    mod = importlib.util.module_from_spec(spec)
    # Patch heavy module-level deps before exec
    with (
        patch.dict(sys.modules, {"findajob.scoring": __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()}),
    ):
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pass
    return mod


def test_null_score_row_rescored_to_scored(db):
    """A manual_review row with null relevance_score is re-scored on the next triage run."""
    _insert_job(db, "fp1", "manual_review", relevance_score=None)

    fake_score = {**GOOD_SCORE}
    fake_latency = 500

    # Import the function under test (extracted from triage.py)
    from triage import score_null_manual_review_rows  # noqa: PLC0415

    with patch("triage.score_job", return_value=(fake_score, fake_latency)):
        count = score_null_manual_review_rows(db, "profile text", "", limit=50)

    assert count == 1
    row = db.execute("SELECT stage, relevance_score FROM jobs WHERE id='fp1'").fetchone()
    assert row["stage"] == "scored"
    assert row["relevance_score"] == 8

    audit = db.execute(
        "SELECT old_value, new_value FROM audit_log WHERE job_id='fp1' AND field_changed='stage'"
    ).fetchone()
    assert audit["old_value"] == "manual_review"
    assert audit["new_value"] == "scored"


def test_real_flag_row_not_retried(db):
    """A manual_review row with a real relevance_score is not re-scored."""
    _insert_job(db, "fp2", "manual_review", relevance_score=5)

    from triage import score_null_manual_review_rows  # noqa: PLC0415

    with patch("triage.score_job") as mock_score:
        score_null_manual_review_rows(db, "profile text", "", limit=50)

    mock_score.assert_not_called()


def test_aged_out_rows_excluded(db):
    """Null-score rows older than 7 days are skipped to avoid retrying genuinely broken JDs."""
    _insert_job(db, "fp3", "manual_review", relevance_score=None, days_old=8)

    from triage import score_null_manual_review_rows  # noqa: PLC0415

    with patch("triage.score_job") as mock_score:
        score_null_manual_review_rows(db, "profile text", "", limit=50)

    mock_score.assert_not_called()


def test_limit_caps_retry_batch(db):
    """limit parameter prevents API flood after a large outage."""
    for i in range(10):
        _insert_job(db, f"fp{i}", "manual_review", relevance_score=None)

    fake_score = {**GOOD_SCORE}

    from triage import score_null_manual_review_rows  # noqa: PLC0415

    with patch("triage.score_job", return_value=(fake_score, 100)):
        count = score_null_manual_review_rows(db, "profile text", "", limit=3)

    assert count == 3
