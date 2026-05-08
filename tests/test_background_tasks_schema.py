"""#554 — Schema-shape tests for ``background_tasks`` table.

Mirrors the pattern in ``test_init_db_schema.py`` and
``test_onboarding_sessions_schema.py``: run the migration runner against
an empty DB, then assert column presence + constraints. Catches schema
drift between ``migrations/0002_background_tasks.sql`` and any code that
reads/writes the table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob.db.migrate import apply_pending


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "schema.db"
    conn = sqlite3.connect(str(path))
    apply_pending(conn)
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, dict[str, object]]:
    return {
        row[1]: {"type": row[2], "notnull": row[3], "default": row[4], "pk": row[5]}
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def test_background_tasks_table_exists(db: sqlite3.Connection) -> None:
    cols = _columns(db, "background_tasks")
    assert cols, "background_tasks table not created"


def test_background_tasks_required_columns(db: sqlite3.Connection) -> None:
    cols = _columns(db, "background_tasks")
    expected = {
        "id",
        "job_id",
        "kind",
        "started_at",
        "finished_at",
        "status",
        "error_message",
        "pid",
    }
    assert expected <= set(cols.keys()), f"missing columns: {expected - set(cols.keys())}"


def test_background_tasks_id_is_primary_key(db: sqlite3.Connection) -> None:
    cols = _columns(db, "background_tasks")
    assert cols["id"]["pk"] == 1


def test_background_tasks_notnull_constraints(db: sqlite3.Connection) -> None:
    cols = _columns(db, "background_tasks")
    assert cols["job_id"]["notnull"] == 1
    assert cols["kind"]["notnull"] == 1
    assert cols["started_at"]["notnull"] == 1
    assert cols["status"]["notnull"] == 1
    # Optional columns
    assert cols["finished_at"]["notnull"] == 0
    assert cols["error_message"]["notnull"] == 0
    assert cols["pid"]["notnull"] == 0


def test_background_tasks_default_status_is_running(db: sqlite3.Connection) -> None:
    """An INSERT that omits status must land at 'running' — the launcher
    helper relies on this."""
    db.execute("INSERT INTO background_tasks (job_id, kind) VALUES ('j', 'prep')")
    row = db.execute("SELECT status FROM background_tasks WHERE job_id='j'").fetchone()
    assert row[0] == "running"


def test_background_tasks_default_started_at_is_now(db: sqlite3.Connection) -> None:
    """An INSERT that omits started_at must populate it — used by every
    launcher path."""
    db.execute("INSERT INTO background_tasks (job_id, kind) VALUES ('j2', 'prep')")
    row = db.execute("SELECT started_at FROM background_tasks WHERE job_id='j2'").fetchone()
    assert row[0] is not None
    assert len(row[0]) > 0


def test_background_tasks_indexes(db: sqlite3.Connection) -> None:
    indexes = _indexes(db, "background_tasks")
    assert "idx_background_tasks_job_id" in indexes
    assert "idx_background_tasks_status_kind" in indexes


def test_background_tasks_in_schema_version_2(db: sqlite3.Connection) -> None:
    """After ``apply_pending``, the head migration is 0002 — schema_version=2."""
    row = db.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()
    assert row is not None
    assert int(row[0]) == 2
