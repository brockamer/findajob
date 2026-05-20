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


# ── #691: rebuild helper must preserve indexes ────────────────────────────
#
# Regression: a constraint-relaxation rebuild that calls
# ``ALTER TABLE ... RENAME`` + ``DROP TABLE`` drops every named index
# attached to the renamed shell. Without re-applying them, the rebuilt
# ``jobs`` runs without ``idx_jobs_fingerprint`` / ``idx_jobs_stage`` /
# etc., silently degrading every dashboard query. Verified empirically
# against sqlite_master before the fix landed.


def _named_indexes(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return non-autoindex names attached to ``table`` (sorted)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? "
        "AND name NOT LIKE 'sqlite_autoindex_%' ORDER BY name",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def test_briefing_ready_rebuild_preserves_jobs_indexes(tmp_path: Path) -> None:
    """The rebuild path on a legacy stack (CHECK without briefing_ready) must
    leave every named ``jobs`` index in place. Without index re-creation
    the rebuilt table runs without ``idx_jobs_fingerprint`` etc.,
    silently degrading every dashboard query.
    """
    db = tmp_path / "rebuild_preserves_indexes.db"

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        indexes_at_head = _named_indexes(conn, "jobs")
        assert indexes_at_head, "fixture-baseline broken: fresh DB should have named jobs indexes"
    finally:
        conn.close()

    # Force the rebuild by simulating the pre-#691 CHECK constraint.
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
        conn.execute("PRAGMA foreign_keys=OFF")
        cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        keep_cols = ["id", "fingerprint", "url", "title", "company", "source", "stage"]
        keep_cols_csv = ",".join(c for c in keep_cols if c in cols)
        conn.execute("ALTER TABLE jobs RENAME TO _jobs_v1_oldcheck")
        conn.executescript(old_check_create.replace("jobs_old_check", "jobs"))
        conn.execute(f"INSERT INTO jobs ({keep_cols_csv}) SELECT {keep_cols_csv} FROM _jobs_v1_oldcheck")
        conn.execute("DROP TABLE _jobs_v1_oldcheck")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        indexes_after_rebuild = _named_indexes(conn, "jobs")
        assert indexes_after_rebuild == indexes_at_head, (
            f"rebuild dropped jobs indexes: had {indexes_at_head}, now {indexes_after_rebuild}"
        )
    finally:
        conn.close()


# ── #723: rebuild must tolerate drift columns in the legacy schema ────────
#
# The operator's pre-M5 production ``jobs`` table carried two columns the
# head schema no longer defines: ``company_signal`` and ``feedback_version``.
# Pre-fix, the rebuild built ``cols_csv`` from ``PRAGMA table_info(jobs)``
# before the rename and tried ``INSERT INTO jobs (..., company_signal, ...,
# feedback_version, ...) SELECT ... FROM _jobs_pre_briefing_ready``. The new
# table has no such columns; SQLite raised ``no such column`` mid-INSERT.
# Because the helper ran outside the per-migration transaction wrapper,
# the rename was already committed: legacy got stuck under its alias and
# the new ``jobs`` stayed empty until the next triage repopulated it from
# scratch — severing every prior ``audit_log`` job_id link. (#723.)
#
# Fix: intersect ``legacy_cols`` with the new table's columns and only
# copy the overlap. Plus row-count invariant + BEGIN/COMMIT wrapper as
# defense in depth.


def test_rebuild_tolerates_drift_columns_absent_from_head_schema(tmp_path: Path) -> None:
    """A legacy ``jobs`` carrying drift columns the head schema doesn't
    define must still rebuild cleanly when the helper fires for an
    unrelated CHECK relaxation. The drift columns are dropped silently;
    row count is preserved; the new schema reaches its expected shape.

    Regression target: #723. A pre-M5 stack carrying drift columns
    on ``jobs`` caused the v0.27 ``briefing_ready`` CHECK relaxation
    to silently abandon all historical rows under the legacy alias —
    the rename committed, the INSERT raised mid-flight on a missing
    column in the new schema, the DROP never ran, the new ``jobs``
    sat empty until the next data-producing run repopulated it from
    scratch.
    """
    db = tmp_path / "drift_columns.db"

    # First: bring the DB to head so a clean jobs table exists.
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    # Now: simulate the operator's pre-v0.27 schema — jobs has the OLD
    # CHECK (no ``briefing_ready``) AND two drift columns the head
    # schema doesn't define.
    conn = sqlite3.connect(str(db))
    try:
        old_with_drift = """
        CREATE TABLE jobs_old_drift (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'test',
            company_signal TEXT DEFAULT '',
            feedback_version TEXT,
            stage TEXT DEFAULT 'discovered' CHECK(stage IN (
                'discovered', 'enriched', 'scored', 'manual_review',
                'prep_in_progress', 'materials_drafted', 'waitlisted', 'applied',
                'response_received', 'interview', 'offer', 'rejected',
                'not_selected', 'withdrawn'
            ))
        )
        """
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE jobs RENAME TO _jobs_v1_clean")
        conn.executescript(old_with_drift.replace("jobs_old_drift", "jobs"))
        # Seed sentinel rows that exercise both shared cols and drift cols.
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, "
            "company_signal, feedback_version) "
            "VALUES ('drift-1', 'fp-drift-1', 'https://x', 'T1', 'C1', 'test', 'applied', "
            "'positive', 'v2024-01-01')"
        )
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) "
            "VALUES ('drift-2', 'fp-drift-2', 'https://y', 'T2', 'C2', 'test', 'scored')"
        )
        conn.execute("DROP TABLE _jobs_v1_clean")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

        # Sanity: drift columns present, CHECK rejects briefing_ready.
        drift_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "company_signal" in drift_cols and "feedback_version" in drift_cols, (
            "fixture-baseline broken: drift columns missing from simulated old jobs"
        )
        assert not _stage_check_accepts(conn, "briefing_ready"), (
            "fixture-baseline broken: simulated-old-state should reject briefing_ready"
        )
    finally:
        conn.close()

    # The actual regression check: apply_pending must rebuild cleanly
    # despite the drift columns, NOT raise no-such-column, NOT silently
    # leave an empty new jobs next to a populated legacy shell.
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        # Drift columns dropped, head shape reached.
        post_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "company_signal" not in post_cols, "drift column 'company_signal' must be dropped by rebuild"
        assert "feedback_version" not in post_cols, "drift column 'feedback_version' must be dropped by rebuild"
        # Both sentinel rows survived (row count + content).
        row_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert row_count == 2, f"sentinel rows lost: expected 2, got {row_count}"
        ids_preserved = {r[0] for r in conn.execute("SELECT id FROM jobs").fetchall()}
        assert ids_preserved == {"drift-1", "drift-2"}, f"specific ids lost: {ids_preserved}"
        # Relaxed CHECK in effect.
        assert _stage_check_accepts(conn, "briefing_ready"), "post-rebuild CHECK still rejects briefing_ready"
        # No legacy table residue.
        assert not _has_table(conn, "_jobs_pre_briefing_ready"), (
            "rebuild left _jobs_pre_briefing_ready behind — atomic-rollback or drop step skipped"
        )
    finally:
        conn.close()


# ── #691: background_tasks.kind gains 'prep_phase_b' ──────────────────────
#
# Phase B is dispatched as a distinct ``background_tasks.kind`` so the
# watchdog can reap stuck Phase B runs into ``briefing_ready`` (preserving
# the briefing folder) instead of ``scored``. Mirror the briefing_ready
# stage-CHECK migration: fresh DB picks it up via 0002; legacy v1 stacks
# absorb it via the rebuild helper.


def _kind_check_accepts(conn: sqlite3.Connection, kind_value: str) -> bool:
    """True iff an INSERT of a background_tasks row with this kind succeeds."""
    try:
        conn.execute(
            "INSERT INTO background_tasks (job_id, kind) VALUES (?, ?)",
            (f"probe-{kind_value}", kind_value),
        )
        conn.execute("DELETE FROM background_tasks WHERE job_id=?", (f"probe-{kind_value}",))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def test_fresh_db_accepts_prep_phase_b_kind(tmp_path: Path) -> None:
    """A fresh DB migrated through ``apply_pending`` accepts
    ``kind='prep_phase_b'`` as a ``background_tasks.kind`` value."""
    db = tmp_path / "fresh_phase_b.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        assert _kind_check_accepts(conn, "prep_phase_b"), "fresh DB must accept kind='prep_phase_b' after apply_pending"
    finally:
        conn.close()


def test_existing_v1_db_gains_prep_phase_b_via_helper(tmp_path: Path) -> None:
    """A stack at schema_version=1 with the OLD ``background_tasks.kind``
    CHECK (no ``prep_phase_b``) gets the constraint updated when
    ``apply_pending`` runs again. Existing rows + indexes preserved."""
    db = tmp_path / "existing_v1_phase_b.db"

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        # Sentinel row to verify preservation across the rebuild.
        conn.execute("INSERT INTO background_tasks (job_id, kind) VALUES ('preserve-2', 'prep')")
        conn.commit()

        # Simulate the pre-#691 CHECK (no prep_phase_b).
        old_check = """
        CREATE TABLE background_tasks_old_check (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('prep', 'interview_prep', 'speculative_research')),
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'succeeded', 'failed')),
            error_message TEXT,
            pid INTEGER
        )
        """
        conn.execute("PRAGMA foreign_keys=OFF")
        cols = [row[1] for row in conn.execute("PRAGMA table_info(background_tasks)").fetchall()]
        cols_csv = ",".join(cols)
        conn.execute("ALTER TABLE background_tasks RENAME TO _bg_v1_oldcheck")
        conn.executescript(old_check.replace("background_tasks_old_check", "background_tasks"))
        conn.execute(f"INSERT INTO background_tasks ({cols_csv}) SELECT {cols_csv} FROM _bg_v1_oldcheck")
        conn.execute("DROP TABLE _bg_v1_oldcheck")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

        assert not _kind_check_accepts(conn, "prep_phase_b"), (
            "test setup broken: simulated-old-state should reject prep_phase_b"
        )
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        assert _kind_check_accepts(conn, "prep_phase_b"), "after apply_pending, kind='prep_phase_b' must be accepted"
        # Sentinel + named indexes preserved.
        row = conn.execute("SELECT job_id, kind FROM background_tasks WHERE job_id='preserve-2'").fetchone()
        assert row == ("preserve-2", "prep")
        assert _named_indexes(conn, "background_tasks") == [
            "idx_background_tasks_job_id",
            "idx_background_tasks_status_kind",
        ], "rebuild must preserve background_tasks named indexes"
    finally:
        conn.close()


def test_prep_phase_b_helper_is_idempotent(tmp_path: Path) -> None:
    """Running ``apply_pending`` twice on a DB that already has
    ``prep_phase_b`` in the CHECK must not trigger a second rebuild.
    """
    db = tmp_path / "idempotent_phase_b.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        conn.execute("INSERT INTO background_tasks (job_id, kind) VALUES ('idem-2', 'prep_phase_b')")
        conn.commit()
        rowid_before = conn.execute("SELECT rowid FROM background_tasks WHERE job_id='idem-2'").fetchone()[0]
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        rowid_after = conn.execute("SELECT rowid FROM background_tasks WHERE job_id='idem-2'").fetchone()[0]
        assert rowid_after == rowid_before, "rebuild ran on second apply_pending — helper is not idempotent"
    finally:
        conn.close()


# ── #498: tighten jobs.score_status CHECK — drop dead 'needs_info' value ───
#
# Constraint *tightening* (vs the relaxations above). The helper's
# pre-rebuild row-count guard satisfies AC#2 ("existing rows verified
# score_status != 'needs_info' before the constraint tightens") and gives
# a specific error message before any rename/create work happens.
# Verified at filing time that all eight cohort stacks carry zero such
# rows; the guard is defense in depth.


def _score_status_check_accepts(conn: sqlite3.Connection, value: str) -> bool:
    """True iff an INSERT of a job row with ``score_status=value`` succeeds."""
    try:
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, score_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"probe-ss-{value}", f"fp-probe-ss-{value}", "https://x.test/", "Probe", "Probe Co", "test", value),
        )
        conn.execute("DELETE FROM jobs WHERE id=?", (f"probe-ss-{value}",))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def test_fresh_db_rejects_needs_info_score_status(tmp_path: Path) -> None:
    """A fresh DB through ``apply_pending`` has the tightened CHECK from
    0001_initial.sql — ``needs_info`` is no longer accepted."""
    db = tmp_path / "fresh_score_status.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        assert _score_status_check_accepts(conn, "scored"), "fresh DB must still accept 'scored'"
        assert _score_status_check_accepts(conn, "manual_review"), "fresh DB must still accept 'manual_review'"
        assert not _score_status_check_accepts(conn, "needs_info"), (
            "fresh DB must reject 'needs_info' — CHECK should be tightened"
        )
    finally:
        conn.close()


def test_existing_v1_db_tightens_score_status_via_helper(tmp_path: Path) -> None:
    """A stack at schema_version=1 with the OLD ``jobs.score_status``
    CHECK (still permitting ``needs_info``) gets the constraint tightened
    when ``apply_pending`` runs again. Existing rows preserved."""
    db = tmp_path / "existing_v1_score_status.db"

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        # Sentinel row to verify preservation across the rebuild.
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, score_status) "
            "VALUES ('preserve-ss', 'fp-preserve-ss', 'https://x', 'T', 'C', 'test', 'scored')"
        )
        conn.commit()

        # Simulate the pre-#498 CHECK (still includes 'needs_info').
        old_check_create = """
        CREATE TABLE jobs_old_check (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'test',
            score_status TEXT CHECK(score_status IN ('scored', 'manual_review', 'needs_info'))
        )
        """
        conn.execute("PRAGMA foreign_keys=OFF")
        cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        keep_cols = ["id", "fingerprint", "url", "title", "company", "source", "score_status"]
        keep_cols_csv = ",".join(c for c in keep_cols if c in cols)
        conn.execute("ALTER TABLE jobs RENAME TO _jobs_v1_oldscore")
        conn.executescript(old_check_create.replace("jobs_old_check", "jobs"))
        conn.execute(f"INSERT INTO jobs ({keep_cols_csv}) SELECT {keep_cols_csv} FROM _jobs_v1_oldscore")
        conn.execute("DROP TABLE _jobs_v1_oldscore")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

        # Sanity check the simulated old state still accepts needs_info.
        assert _score_status_check_accepts(conn, "needs_info"), (
            "test setup is broken: simulated-old-state should accept 'needs_info'"
        )
    finally:
        conn.close()

    # Re-run apply_pending; the new helper must tighten the CHECK.
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        assert not _score_status_check_accepts(conn, "needs_info"), "after apply_pending, 'needs_info' must be rejected"
        row = conn.execute("SELECT id, score_status FROM jobs WHERE id='preserve-ss'").fetchone()
        assert row == ("preserve-ss", "scored"), "existing row must survive the rebuild"
    finally:
        conn.close()


def test_score_status_helper_refuses_when_legacy_rows_exist(tmp_path: Path) -> None:
    """If any row carries ``score_status='needs_info'`` when the helper
    runs, the rebuild must refuse with a specific error before any
    rename/create work happens. Satisfies AC#2 of #498.
    """
    db = tmp_path / "legacy_rows_score_status.db"

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    # Rewrite jobs with the OLD CHECK so we can insert a 'needs_info' row.
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
            score_status TEXT CHECK(score_status IN ('scored', 'manual_review', 'needs_info'))
        )
        """
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE jobs RENAME TO _jobs_v1_legacy_rows")
        conn.executescript(old_check_create.replace("jobs_old_check", "jobs"))
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, score_status) "
            "VALUES ('legacy-needs-info', 'fp-legacy-needs-info', 'https://x', 'T', 'C', 'test', 'needs_info')"
        )
        conn.execute("DROP TABLE _jobs_v1_legacy_rows")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
    finally:
        conn.close()

    # apply_pending must refuse rather than silently drop or fail with a
    # generic CHECK violation buried inside _rebuild_table_with_indexes.
    conn = sqlite3.connect(str(db))
    try:
        with pytest.raises(RuntimeError, match="needs_info"):
            apply_pending(conn)
        # Row is still there, untouched — fail-closed.
        row = conn.execute("SELECT id, score_status FROM jobs WHERE id='legacy-needs-info'").fetchone()
        assert row == ("legacy-needs-info", "needs_info"), "legacy row must not be mutated by the refused rebuild"
    finally:
        conn.close()


def test_score_status_helper_is_idempotent(tmp_path: Path) -> None:
    """Running ``apply_pending`` twice on a DB whose CHECK is already
    tightened must not trigger a second rebuild.
    """
    db = tmp_path / "idempotent_score_status.db"
    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, score_status) "
            "VALUES ('idem-ss', 'fp-idem-ss', 'https://x', 'T', 'C', 'test', 'scored')"
        )
        conn.commit()
        rowid_before = conn.execute("SELECT rowid FROM jobs WHERE id='idem-ss'").fetchone()[0]
    finally:
        conn.close()

    conn = sqlite3.connect(str(db))
    try:
        apply_pending(conn)
        rowid_after = conn.execute("SELECT rowid FROM jobs WHERE id='idem-ss'").fetchone()[0]
        assert rowid_after == rowid_before, "rebuild ran on second apply_pending — helper is not idempotent"
    finally:
        conn.close()
