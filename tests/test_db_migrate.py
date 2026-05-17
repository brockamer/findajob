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


# ── #560: documented-dead column drift tripwires ────────────────────────────
#
# ``company_signal`` and ``feedback_version`` are columns that exist in the
# operator's pre-M5 production stack but are absent from ``0001_initial.sql``.
# Both are dead (no current code references; ``feedback_version`` retains
# 196 rows of historical data on production). The migration file documents
# them as intentionally-absent in its §"Intentionally-absent columns" block.
#
# These tests are tripwires: if ``src/`` or ``scripts/`` add a reference to
# either name, OR if 0001 silently grows the column back, the introducer
# must revisit the drift decision (re-add to 0001? new migration? drop?).

_DOCUMENTED_DEAD_COLUMNS: tuple[str, ...] = ("company_signal", "feedback_version")


def test_dead_columns_absent_from_0001() -> None:
    """0001_initial.sql must not list either documented-dead column.

    The fix in #560 is documentation-only — the columns stay absent from
    fresh installs and stay present on existing stacks (preserving the
    historical ``feedback_version`` data). Adding them to 0001 silently
    would invalidate the drift documentation in the file's header.
    """
    initial_sql = (MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8")
    # Strip comment lines so the documentation block in the header doesn't
    # false-positive — only DDL bodies matter.
    ddl_only = "\n".join(line for line in initial_sql.splitlines() if not line.lstrip().startswith("--"))
    for col in _DOCUMENTED_DEAD_COLUMNS:
        assert col not in ddl_only, (
            f"Documented-dead column {col!r} appeared in 0001_initial.sql DDL. "
            f"See the §'Intentionally-absent columns' block in that file (#560)."
        )


def test_dead_columns_absent_from_tracked_code() -> None:
    """No tracked Python file in ``src/findajob`` or ``scripts`` references
    either documented-dead column.

    If a future feature wants to revive one, this test fires. The right
    fix is then either: (a) ship a numbered migration that adds the column
    via ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``, OR (b) explain in
    the issue/PR why the dead-status documentation is wrong.
    """
    repo_root = Path(__file__).resolve().parent.parent
    found: list[str] = []
    for root in (repo_root / "src" / "findajob", repo_root / "scripts"):
        for py in root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for col in _DOCUMENTED_DEAD_COLUMNS:
                if col in text:
                    found.append(f"{py.relative_to(repo_root)}: {col!r}")
    assert not found, (
        "Documented-dead column references found in tracked code (#560):\n"
        + "\n".join(f"  {f}" for f in found)
        + "\nSee migrations/0001_initial.sql §'Intentionally-absent columns'."
    )


# ── #691: briefing-first gate — new ``briefing_ready`` stage value ─────────
#
# The gate splits ``_run_prep`` into Phase A (briefing only) + Phase B
# (continue-prep). The interstitial state is a new ``jobs.stage`` value
# ``'briefing_ready'``. SQLite can't ALTER a CHECK constraint in place;
# the runner adds a Python helper that does the rename-create-copy-drop
# rebuild from the canonical 0001_initial.sql template. The helper runs
# from ``apply_pending`` on every connect so existing stacks at
# ``schema_version=1`` pick up the new constraint without a version bump.


def _stage_check_accepts(conn: sqlite3.Connection, stage_value: str) -> bool:
    """True iff an INSERT of a job row with ``stage=stage_value`` succeeds."""
    try:
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"probe-{stage_value}",
                f"fp-probe-{stage_value}",
                "https://x.test/",
                "Probe",
                "Probe Co",
                "test",
                stage_value,
            ),
        )
        conn.execute("DELETE FROM jobs WHERE id=?", (f"probe-{stage_value}",))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def test_fresh_db_accepts_briefing_ready_stage(tmp_path: Path) -> None:
    """A fresh DB migrated through ``apply_pending`` accepts ``briefing_ready``
    as a ``jobs.stage`` value — the 0001_initial.sql CHECK constraint
    includes it.
    """
    db = tmp_path / "fresh_briefing.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        assert _stage_check_accepts(conn, "briefing_ready"), (
            "fresh DB must accept stage='briefing_ready' after apply_pending"
        )
    finally:
        conn.close()


def test_existing_v1_db_gains_briefing_ready_via_helper(tmp_path: Path) -> None:
    """A stack already at schema_version=1 with the OLD CHECK constraint
    (no ``briefing_ready``) gets the constraint updated when
    ``apply_pending`` runs again. Existing rows are preserved.

    This is the migration path for every shipped tester stack — they're
    all at version 1 with the pre-#691 CHECK and need to absorb the new
    constraint without losing data.
    """
    db = tmp_path / "existing_v1.db"

    # First: bring the DB to current head (gives us a v1 jobs table with
    # whatever CHECK is in 0001_initial.sql, which post-#691 includes
    # briefing_ready).
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    # Now: simulate a pre-#691 stack by rewriting jobs with the OLD CHECK
    # (without briefing_ready). The new helper must detect this and rebuild.
    conn = sqlite3.connect(str(db))
    try:
        old_check_create = """
        CREATE TABLE jobs_old_check (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'test',
            stage TEXT DEFAULT 'discovered' CHECK(stage IN (
                'discovered', 'enriched', 'scored', 'manual_review',
                'prep_in_progress', 'materials_drafted', 'waitlisted', 'applied',
                'response_received', 'interview', 'offer', 'rejected',
                'not_selected', 'withdrawn'
            ))
        )
        """
        # Capture an existing-row sentinel to verify preservation
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('preserve-1', 'fp-preserve-1', 'https://x', 'T', 'C', 'test', 'scored')"
        )
        conn.commit()
        sentinel = conn.execute("SELECT id, stage FROM jobs WHERE id='preserve-1'").fetchone()
        assert sentinel == ("preserve-1", "scored")

        # Rebuild jobs with the OLD CHECK (no briefing_ready), preserving
        # the sentinel row. This simulates the shipped v1 schema.
        conn.execute("PRAGMA foreign_keys=OFF")
        cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        # Filter to the subset present in old_check_create
        keep_cols = ["id", "fingerprint", "url", "title", "company", "source", "stage"]
        keep_cols_csv = ",".join(c for c in keep_cols if c in cols)
        conn.execute("ALTER TABLE jobs RENAME TO _jobs_v1_oldcheck")
        conn.executescript(old_check_create.replace("jobs_old_check", "jobs"))
        conn.execute(f"INSERT INTO jobs ({keep_cols_csv}) SELECT {keep_cols_csv} FROM _jobs_v1_oldcheck")
        conn.execute("DROP TABLE _jobs_v1_oldcheck")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

        # Sanity check the simulated old state
        assert not _stage_check_accepts(conn, "briefing_ready"), (
            "test setup is broken: simulated-old-state should reject briefing_ready"
        )
    finally:
        conn.close()

    # Re-run apply_pending; the new helper must add briefing_ready to the CHECK.
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        assert _stage_check_accepts(conn, "briefing_ready"), "after apply_pending, briefing_ready must be accepted"
        # Existing row preserved
        row = conn.execute("SELECT id, stage FROM jobs WHERE id='preserve-1'").fetchone()
        assert row == ("preserve-1", "scored"), "existing row must survive the rebuild"
    finally:
        conn.close()


def test_briefing_ready_helper_is_idempotent(tmp_path: Path) -> None:
    """Running ``apply_pending`` twice on a DB that already has
    ``briefing_ready`` in the CHECK constraint must not trigger a second
    rebuild. The helper short-circuits.
    """
    db = tmp_path / "idempotent_briefing.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        # Insert a row that depends on the table's identity surviving
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('idem-1', 'fp-idem-1', 'https://x', 'T', 'C', 'test', 'briefing_ready')"
        )
        conn.commit()
        rowid_before = conn.execute("SELECT rowid FROM jobs WHERE id='idem-1'").fetchone()[0]
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        # If the helper had rebuilt the table needlessly, the rowid may shift.
        # (A rebuild via INSERT INTO ... SELECT does NOT preserve rowids; this
        # is the cheapest probe that detects a non-no-op second run.)
        rowid_after = conn.execute("SELECT rowid FROM jobs WHERE id='idem-1'").fetchone()[0]
        assert rowid_after == rowid_before, "rebuild ran on second apply_pending — helper is not idempotent"
    finally:
        conn.close()
