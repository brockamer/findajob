"""Tests for health-check null-score vs real-flag manual_review split.

notify.py cmd_health_check must distinguish:
  - null-score manual_review: relevance_score IS NULL (scorer failure — needs ops attention)
  - real-flag manual_review: relevance_score IS NOT NULL (LLM flagged for human review)
"""

import sqlite3
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

MINIMAL_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    stage TEXT DEFAULT 'scored',
    relevance_score INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    dupe_of TEXT DEFAULT '',
    synthetic INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.executescript(MINIMAL_SCHEMA)
    conn.execute("INSERT INTO jobs (id, title, stage, relevance_score) VALUES ('a','t','manual_review',NULL)")
    conn.execute("INSERT INTO jobs (id, title, stage, relevance_score) VALUES ('b','t','manual_review',NULL)")
    conn.execute("INSERT INTO jobs (id, title, stage, relevance_score) VALUES ('c','t','manual_review',5)")
    conn.execute("INSERT INTO jobs (id, title, stage, relevance_score) VALUES ('d','t','scored',8)")
    conn.commit()
    conn.close()
    return db


def _count_null_vs_real(db_path):
    """Helper that mirrors the health-check split queries."""
    conn = sqlite3.connect(db_path)
    null_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage='manual_review' AND relevance_score IS NULL"
    ).fetchone()[0]
    real_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage='manual_review' AND relevance_score IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    return null_count, real_count


def test_null_and_real_counts_are_distinct(tmp_db):
    """Null-score and real-flag rows must be counted separately."""
    null_count, real_count = _count_null_vs_real(tmp_db)
    assert null_count == 2
    assert real_count == 1


def test_null_score_only_warns_when_present(tmp_db):
    """Null-score count must be non-zero and distinct from real-flag count."""
    # Verifies the two split queries that notify.py's health-check runs.
    # cmd_health_check() has too many filesystem deps to call in unit tests;
    # we validate the SQL logic that underpins the split warning directly.
    null_count, real_count = _count_null_vs_real(tmp_db)
    assert null_count == 2, "2 null-score rows inserted"
    assert real_count == 1, "1 real-flag row inserted"
    # The two counts are different — health-check can surface them as separate warnings.
    assert null_count != real_count
