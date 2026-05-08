"""Schema tests for onboarding_sessions table (#336 Task 1).

Mirrors the fixture pattern in test_init_db_schema.py: run init_db.py against
a scratch BASE, then assert column presence + idempotency.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path):
    base = tmp_path / "repo"
    (base / "data").mkdir(parents=True)
    (base / "src" / "findajob").mkdir(parents=True)
    (base / "src" / "findajob" / "__init__.py").write_text("")
    (base / "src" / "findajob" / "paths.py").write_text(f'BASE = r"{base}"\n')
    repo_root = Path(__file__).resolve().parents[1]
    (base / "src" / "findajob" / "db.py").write_text((repo_root / "src" / "findajob" / "db.py").read_text())

    env = os.environ.copy()
    env["PYTHONPATH"] = str(base / "src")

    init_db = repo_root / "scripts" / "init_db.py"

    result = subprocess.run(
        [sys.executable, str(init_db)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"init_db.py failed: {result.stderr}"
    return base, init_db, env


def _columns(db_path: Path, table: str) -> dict[str, dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            row[1]: {"type": row[2], "notnull": row[3], "default": row[4], "pk": row[5]}
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
    finally:
        conn.close()


def test_onboarding_sessions_table_exists(fresh_db):
    base, _, _ = fresh_db
    cols = _columns(base / "data" / "pipeline.db", "onboarding_sessions")
    assert cols, "onboarding_sessions table not created"


def test_onboarding_sessions_required_columns(fresh_db):
    base, _, _ = fresh_db
    cols = _columns(base / "data" / "pipeline.db", "onboarding_sessions")
    expected = {
        "id",
        "history_json",
        "captured_blocks_json",
        "started_at",
        "last_turn_at",
        "completed_at",
        "error_state",
    }
    assert expected <= set(cols.keys()), f"missing columns: {expected - set(cols.keys())}"


def test_onboarding_sessions_id_is_primary_key(fresh_db):
    base, _, _ = fresh_db
    cols = _columns(base / "data" / "pipeline.db", "onboarding_sessions")
    assert cols["id"]["pk"] == 1


def test_onboarding_sessions_notnull_constraints(fresh_db):
    base, _, _ = fresh_db
    cols = _columns(base / "data" / "pipeline.db", "onboarding_sessions")
    assert cols["history_json"]["notnull"] == 1
    assert cols["captured_blocks_json"]["notnull"] == 1
    assert cols["started_at"]["notnull"] == 1
    assert cols["last_turn_at"]["notnull"] == 1
    assert cols["completed_at"]["notnull"] == 0
    assert cols["error_state"]["notnull"] == 0


def test_init_db_idempotent(fresh_db):
    """Running init_db.py twice must not error or duplicate rows/tables."""
    base, init_db, env = fresh_db
    result = subprocess.run(
        [sys.executable, str(init_db)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"second init_db.py run failed: {result.stderr}"


def test_onboarding_sessions_insert_and_read_roundtrip(fresh_db):
    base, _, _ = fresh_db
    db_path = base / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO onboarding_sessions
                (id, history_json, started_at, last_turn_at)
            VALUES (?, ?, ?, ?)
            """,
            ("test-uuid-1", '[{"role":"assistant","content":"hi"}]', "2026-05-01T00:00:00Z", "2026-05-01T00:00:00Z"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, history_json, captured_blocks_json, completed_at, error_state "
            "FROM onboarding_sessions WHERE id = ?",
            ("test-uuid-1",),
        ).fetchone()
        assert row[0] == "test-uuid-1"
        assert row[1] == '[{"role":"assistant","content":"hi"}]'
        assert row[2] == "{}"
        assert row[3] is None
        assert row[4] is None
    finally:
        conn.close()
