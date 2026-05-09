"""Schema baseline for migration 0003: rejection_suggestions table."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from findajob.db.migrate import apply_pending


def test_0003_creates_rejection_suggestions_table(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(rejection_suggestions)").fetchall()}
    expected = {
        "id",
        "gmail_message_id",
        "received_at",
        "detected_at",
        "sender",
        "subject",
        "body_excerpt",
        "extracted_company",
        "extracted_role",
        "matched_job_id",
        "match_status",
        "confidence",
        "suggested_reason",
        "user_action",
        "user_action_at",
        "user_chose_job_id",
    }
    missing = expected - cols
    assert not missing, f"Migration 0003 missing columns: {missing}"

    indexes = {row[1] for row in conn.execute("PRAGMA index_list(rejection_suggestions)").fetchall()}
    assert "rejection_suggestions_user_action" in indexes
    assert "rejection_suggestions_matched_job" in indexes

    schema_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='rejection_suggestions'"
    ).fetchone()[0]
    assert "gmail_message_id TEXT NOT NULL UNIQUE" in schema_sql

    conn.close()


def test_0003_idempotent_replay(tmp_path: Path) -> None:
    """Applying twice on the same DB must not error (CREATE IF NOT EXISTS semantics)."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)
    apply_pending(conn)
    conn.close()


def test_0003_bumps_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)
    version = int(conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()[0])
    assert version >= 3, f"schema_version={version} — 0003 did not run"
    conn.close()
