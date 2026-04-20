"""Tests that init_db.py is the single source of truth for the SQLite schema.

Each test creates a fresh DB via init_db.py, then asserts that every column
written by production code (cost_tracking.log_call, poll_flags user_notes
writes, etc.) exists on the freshly-initialized DB.

When a one-shot migration is introduced (scripts/migrate_*.py), add a new
test here asserting init_db.py covers its columns — otherwise fresh deploys
will crash at the first production write to those columns.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Run init_db.py against a scratch BASE and return the DB path."""
    base = tmp_path / "repo"
    (base / "data").mkdir(parents=True)
    (base / "src" / "findajob").mkdir(parents=True)
    # Provide a minimal findajob.paths module so init_db.py's import resolves.
    (base / "src" / "findajob" / "__init__.py").write_text("")
    (base / "src" / "findajob" / "paths.py").write_text(f'BASE = r"{base}"\n')

    env = os.environ.copy()
    env["PYTHONPATH"] = str(base / "src")

    repo_root = Path(__file__).resolve().parents[1]
    init_db = repo_root / "scripts" / "init_db.py"

    result = subprocess.run(
        [sys.executable, str(init_db)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"init_db.py failed: {result.stderr}"
    return base / "data" / "pipeline.db"


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def test_cost_log_has_token_and_cost_columns(fresh_db):
    """cost_tracking.log_call inserts into input_tokens, output_tokens, cost_usd."""
    cols = _columns(fresh_db, "cost_log")
    assert "input_tokens" in cols
    assert "output_tokens" in cols
    assert "cost_usd" in cols


def test_jobs_has_user_notes_column(fresh_db):
    """poll_flags.py writes to jobs.user_notes from Applied-tab sheet edits."""
    cols = _columns(fresh_db, "jobs")
    assert "user_notes" in cols
