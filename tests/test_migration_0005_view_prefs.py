"""Schema baseline for migration 0005: view_prefs table."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob.db.migrate import apply_pending


def test_0005_creates_view_prefs_table(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(view_prefs)").fetchall()}
    assert cols == {"tab", "query_string", "updated_at"}

    schema_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='view_prefs'").fetchone()[0]
    assert "tab TEXT PRIMARY KEY" in schema_sql
    assert "query_string TEXT NOT NULL" in schema_sql
    assert "updated_at TEXT NOT NULL" in schema_sql

    conn.close()


def test_0005_tab_check_constraint_allowlist(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)

    for tab in ("dashboard", "applied", "review", "waitlist", "rejected", "not_selected", "archive"):
        conn.execute("INSERT INTO view_prefs(tab, query_string) VALUES(?, ?)", (tab, "cols=title"))
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO view_prefs(tab, query_string) VALUES(?, ?)", ("bogus", "cols=title"))

    conn.close()


def test_0005_idempotent_replay(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)
    apply_pending(conn)
    conn.close()


def test_0005_bumps_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    apply_pending(conn)
    version = int(conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()[0])
    assert version >= 5, f"schema_version={version} — 0005 did not run"
    conn.close()
