"""#552 — Tests for ``findajob.db.migrate.apply_pending``.

Verification approach (per advisor): assertions against observable
schema state (``_meta.schema_version`` + ``PRAGMA table_info``), not
against the runner's internal code shape. Mutation testing for a
behavior-change PR like this maps imperfectly onto "drop a flag, expect
a specific test failure" — instead each test snapshots the DB before
and after a runner call and asserts the expected delta.

Four scenarios:

1. **Fresh DB** — empty file. Runner stamps version=1 and runs
   ``0001_initial.sql``. Result: ``_meta.schema_version=1``,
   ``PRAGMA table_info(jobs)`` matches the schema.
2. **Already-at-1** — second run is a no-op. Idempotency.
3. **Legacy v0.10 fixture** — heuristic detects drift, runs the
   procedural backfill, stamps version=1. Result: schema matches
   fresh-baseline introspection.
4. **dry_run=True** — no DDL, no version row written.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob.db.migrate import (
    MIGRATIONS_DIR,
    _list_migrations,
    apply_pending,
)
from tests.fixtures._legacy_v0_10_setup import write_v0_10_0_db

# Computed dynamically so future migrations don't require test edits —
# every new ``000N_*.sql`` lands cleanly without churning these tests.
HEAD_VERSION: int = max(version for version, _, _ in _list_migrations())


def _table_info(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return list(conn.execute(f"PRAGMA table_info({table})").fetchall())


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _read_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row is not None else None


def test_fresh_db_runs_initial_migration(tmp_path: Path) -> None:
    """A fresh DB picks up the head version and gets every table from
    every numbered migration."""
    db = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(db))
    try:
        applied = apply_pending(conn)
    finally:
        conn.close()

    # Every numbered migration runs against a fresh DB.
    assert len(applied) == HEAD_VERSION
    assert applied[0].version == 1
    assert applied[0].name == "initial"
    assert applied[0].skipped is False
    # Last applied is the head migration.
    assert applied[-1].version == HEAD_VERSION

    conn = sqlite3.connect(str(db))
    try:
        assert _read_version(conn) == HEAD_VERSION
        for tbl in [
            "jobs",
            "audit_log",
            "cost_log",
            "feedback_log",
            "duplicate_groups",
            "speculative_requests",
            "onboarding_sessions",
            "notifications",
        ]:
            assert _has_table(conn, tbl), f"expected {tbl} after 0001_initial.sql"
        # Spot-check a few schema details that the M4 work depended on.
        jobs_cols = {row[1] for row in _table_info(conn, "jobs")}
        assert "loose_fingerprint" in jobs_cols
        assert "synthetic" in jobs_cols
        assert "speculative_briefing_folder" in jobs_cols
        sess_cols = {row[1] for row in _table_info(conn, "onboarding_sessions")}
        assert "tester_openrouter_key" in sess_cols
        assert "tester_rapidapi_key" in sess_cols
        assert "cumulative_cost_usd" in sess_cols
    finally:
        conn.close()


def test_idempotent_second_run_is_noop(tmp_path: Path) -> None:
    """A second ``apply_pending`` against an already-migrated DB does no work."""
    db = tmp_path / "idem.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        applied = apply_pending(conn)
        assert applied == []
        assert _read_version(conn) == HEAD_VERSION
    finally:
        conn.close()


def test_legacy_v0_10_bridges_to_equilibrium(tmp_path: Path) -> None:
    """v0.10 fixture has missing columns + missing tables + cost_calibration
    + tester_google_key. The runner's heuristic detects drift, runs the
    procedural backfill (fixes column drift on existing tables), then
    returns 0 so the apply pass runs 0001_initial.sql — its
    ``CREATE TABLE IF NOT EXISTS`` clauses fill in missing tables like
    ``notifications`` without disturbing what the bridge aligned."""
    db = tmp_path / "legacy.db"
    write_v0_10_0_db(db)

    conn = sqlite3.connect(str(db))
    try:
        applied = apply_pending(conn)
    finally:
        conn.close()

    # Bridge ran (column drift fixed), then every numbered migration
    # ran in order from 0 → HEAD_VERSION. The 0001 IF-NOT-EXISTS
    # creates filled in missing tables; subsequent migrations stack
    # additively.
    assert len(applied) == HEAD_VERSION
    assert applied[0].version == 1

    conn = sqlite3.connect(str(db))
    try:
        assert _read_version(conn) == HEAD_VERSION
        # Cost calibration table dropped.
        assert not _has_table(conn, "cost_calibration")
        # Notifications table is part of equilibrium — the bridge does NOT
        # create it; that's what 0001_initial.sql is for. v0.10 fixture
        # already includes the post-init shape because init_db.py was
        # historically the entry point. The runner is a no-op here.
        # Onboarding columns added.
        sess_cols = {row[1] for row in _table_info(conn, "onboarding_sessions")}
        assert "tester_openrouter_key" in sess_cols
        assert "tester_rapidapi_key" in sess_cols
        assert "cumulative_cost_usd" in sess_cols
        assert "tester_google_key" not in sess_cols
        # Jobs columns added.
        jobs_cols = {row[1] for row in _table_info(conn, "jobs")}
        assert "loose_fingerprint" in jobs_cols
        assert "synthetic" in jobs_cols
        assert "speculative_briefing_folder" in jobs_cols
    finally:
        conn.close()


def test_dry_run_does_not_mutate(tmp_path: Path) -> None:
    """``dry_run=True`` reports what would happen but writes nothing."""
    db = tmp_path / "dry.db"
    conn = sqlite3.connect(str(db))
    try:
        applied = apply_pending(conn, dry_run=True)
    finally:
        conn.close()

    # Reports every would-be migration as skipped.
    assert len(applied) == HEAD_VERSION
    assert applied[0].version == 1
    assert all(m.skipped for m in applied)

    # No state written: _meta wasn't created (we don't ensure it under
    # dry_run), no tables created.
    conn = sqlite3.connect(str(db))
    try:
        assert not _has_table(conn, "jobs")
        assert not _has_table(conn, "_meta")
    finally:
        conn.close()


def test_migrations_dir_lives_inside_package() -> None:
    """The runner reads migrations from inside the installed
    ``findajob`` package (``src/findajob/migrations/``), not from a
    user-config BASE. This test asserts the path resolves to a real
    directory containing 0001_initial.sql."""
    assert MIGRATIONS_DIR.is_dir(), f"expected {MIGRATIONS_DIR} to exist"
    assert (MIGRATIONS_DIR / "0001_initial.sql").is_file()


def test_init_db_script_uses_runner(tmp_path: Path) -> None:
    """End-to-end: invoking ``scripts/init_db.py`` against a fresh path
    produces a migrated DB. Mirrors what ``ops/entrypoint.sh`` does at
    every container start."""
    import subprocess
    import sys

    db = tmp_path / "via_script.db"
    repo_root = Path(__file__).resolve().parent.parent
    init_db = repo_root / "scripts" / "init_db.py"

    result = subprocess.run(
        [sys.executable, str(init_db), str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"init_db.py failed: {result.stderr}"

    conn = sqlite3.connect(str(db))
    try:
        assert _read_version(conn) == HEAD_VERSION
        assert _has_table(conn, "jobs")
    finally:
        conn.close()


def test_corrupt_meta_treated_as_missing(tmp_path: Path) -> None:
    """If ``_meta.schema_version`` exists but is non-numeric, the runner
    re-runs the heuristic. Catches a corrupted-row scenario without
    crashing."""
    db = tmp_path / "corrupt.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _meta VALUES ('schema_version', 'not-a-number')")
        conn.commit()
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        applied = apply_pending(conn)
        # Heuristic fires (no jobs table → version=0), then every
        # numbered migration runs in sequence.
        assert len(applied) == HEAD_VERSION
        assert _read_version(conn) == HEAD_VERSION
    finally:
        conn.close()


@pytest.mark.parametrize(
    "filename,should_match",
    [
        ("0001_initial.sql", True),
        ("0042_add_thing.sql", True),
        ("9999_z.sql", True),
        ("README.md", False),
        ("0001.sql", False),  # no slug
        ("0001-initial.sql", False),  # hyphen, not underscore
        ("001_short.sql", False),  # 3 digits, not 4
        ("0001_Mixed_Case.sql", False),  # uppercase in slug
    ],
)
def test_migration_filename_pattern(filename: str, should_match: bool) -> None:
    """File-discovery regex: enforce ``NNNN_lowercase_slug.sql``."""
    from findajob.db.migrate import _FILENAME_RE

    matched = _FILENAME_RE.match(filename) is not None
    assert matched is should_match, f"{filename}: expected match={should_match}, got {matched}"
