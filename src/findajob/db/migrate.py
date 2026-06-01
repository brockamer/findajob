"""Versioned migration runner for the pipeline database.

Replaces the scattered migration logic that used to live in three places:

- Inline ``ALTER TABLE`` calls in ``scripts/init_db.py``.
- ``migrate_schema()`` in ``findajob.onboarding.session_store`` invoked at
  FastAPI startup from ``findajob.web.app``.
- The migration-arc commentary captured in ``tests/fixtures/_legacy_v0_10_setup.py``.

After M5 there is exactly one entry point: :func:`apply_pending`, called
from ``scripts/init_db.py`` at every container start (via
``ops/entrypoint.sh``). Schema state is tracked by ``_meta.schema_version``,
a single integer.

Algorithm:

1. Ensure the ``_meta`` table exists.
2. Read ``_meta.schema_version``.
   - If absent, run the **heuristic backfill** to determine the stack's
     starting state and stamp it. See :func:`_infer_baseline_version`.
3. Apply every numbered migration with version > current. Transaction
   control (``BEGIN``/``COMMIT``) is embedded in the script handed to
   ``executescript`` — not hand-rolled around it, which ``executescript``'s
   implicit pre-COMMIT silently defeats. The ``schema_version`` bump is
   written inside that same transaction, so a migration's statements and
   its version row commit (or roll back) as one unit and a half-applied
   migration can never leave the DB stamped at the later version.

The heuristic exists because real production stacks have no ``_meta``
table on first contact with this runner. The migration runner bumps
every active deployment to the current minor; first boot under that
image hits the heuristic exactly once, then ``_meta.schema_version=1``
and all subsequent boots are no-ops.

Migration files live in ``$BASE/migrations/{NNNN}_{slug}.sql``. Numbering
starts at 0001. The scheme is intentionally append-only: never rename or
edit an applied migration; ship a new numbered file instead.

Because the runner now wraps each migration in a transaction (step 3), a
``.sql`` migration must NOT contain connection-level PRAGMAs that only
take effect outside a transaction — ``PRAGMA foreign_keys=...`` and
``PRAGMA journal_mode=...`` are silently ignored mid-transaction. If a
future migration needs FK enforcement toggled (e.g. for a
rename-create-copy-drop table rebuild), do it in a Python hook around the
apply pass, not inside the migration script.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR: Path = Path(__file__).resolve().parent.parent / "migrations"
"""Filesystem location of numbered migration SQL files.

Resolved relative to the installed ``findajob`` package — migrations
ship with the code, not with user-config BASE. This lets the runner
work identically against any BASE, including the scratch trees that
test fixtures set up via ``JSP_BASE``.
"""

_FILENAME_RE: re.Pattern[str] = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
"""``NNNN_slug.sql`` — four-digit zero-padded version + lowercase slug."""

# ── Heuristic markers — what columns/tables prove "equilibrium" shape ────
#
# These mirror the current ``init_db.py`` schema as of M5.E1. A stack
# satisfying every check is at version 1 already; no DDL needs to run.
# A stack failing any check is in legacy drift and requires the
# procedural backfill below before we stamp version=1.

_REQUIRED_JOBS_COLUMNS: tuple[str, ...] = (
    "loose_fingerprint",
    "synthetic",
    "speculative_briefing_folder",
)
_REQUIRED_ONBOARDING_SESSIONS_COLUMNS: tuple[str, ...] = (
    "user_openrouter_key",
    "user_rapidapi_key",
    "user_gemini_api_key",
    "cumulative_cost_usd",
)
_REMOVED_ONBOARDING_SESSIONS_COLUMNS: tuple[str, ...] = (
    "tester_google_key",
    "tester_openrouter_key",  # renamed to user_openrouter_key in 0006
    "tester_rapidapi_key",  # renamed to user_rapidapi_key in 0006
)
_REQUIRED_TABLES: tuple[str, ...] = ("notifications", "notes_history")
_REMOVED_TABLES: tuple[str, ...] = ("cost_calibration",)


@dataclass(frozen=True)
class AppliedMigration:
    """Metadata about a single migration that ran (or would run, in dry_run)."""

    version: int
    name: str
    path: Path
    skipped: bool = False
    """True when ``dry_run`` is set or the heuristic fast-pathed past this version."""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    """Create ``_meta`` if absent. Idempotent."""
    conn.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")


def _read_schema_version(conn: sqlite3.Connection) -> int | None:
    """Return current schema_version or None if the row doesn't exist yet."""
    row = conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _write_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO _meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(version),),
    )


def _is_equilibrium(conn: sqlite3.Connection) -> bool:
    """True iff the schema already matches the head migration's shape.

    All five conditions must hold:
    1. ``jobs`` has every column in :data:`_REQUIRED_JOBS_COLUMNS`.
    2. ``onboarding_sessions`` has every column in
       :data:`_REQUIRED_ONBOARDING_SESSIONS_COLUMNS`.
    3. ``onboarding_sessions`` has none of the columns in
       :data:`_REMOVED_ONBOARDING_SESSIONS_COLUMNS`.
    4. Every table in :data:`_REQUIRED_TABLES` exists.
    5. No table in :data:`_REMOVED_TABLES` exists.
    """
    jobs_cols = _columns(conn, "jobs")
    if not jobs_cols:
        return False  # no jobs table → fresh DB, not equilibrium
    if not all(c in jobs_cols for c in _REQUIRED_JOBS_COLUMNS):
        return False
    sess_cols = _columns(conn, "onboarding_sessions")
    if not all(c in sess_cols for c in _REQUIRED_ONBOARDING_SESSIONS_COLUMNS):
        return False
    if any(c in sess_cols for c in _REMOVED_ONBOARDING_SESSIONS_COLUMNS):
        return False
    if not all(_table_exists(conn, t) for t in _REQUIRED_TABLES):
        return False
    if any(_table_exists(conn, t) for t in _REMOVED_TABLES):
        return False
    return True


def _bridge_legacy_to_v1(conn: sqlite3.Connection) -> None:
    """Bring a legacy-shape DB up to the v1 equilibrium.

    Only runs when ``_meta`` is absent AND the DB has tables (so it's
    legacy drift, not a fresh install) AND the equilibrium check failed.

    Each step is idempotent:
    - ``ALTER TABLE ... ADD COLUMN`` is wrapped with a PRAGMA-based
      pre-check so it never errors on a column that already exists.
    - ``DROP TABLE IF EXISTS`` is naturally idempotent.
    - ``DROP COLUMN`` requires SQLite 3.35+; we pre-check existence.

    This function is the durable handler for the v0.10 → v0.20 column
    drift documented in ``tests/fixtures/_legacy_v0_10_setup.py``.
    """
    jobs_cols = _columns(conn, "jobs")
    if "jobs" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        if "loose_fingerprint" not in jobs_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN loose_fingerprint TEXT")
        if "synthetic" not in jobs_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0")
        if "speculative_briefing_folder" not in jobs_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN speculative_briefing_folder TEXT")
        # SQLite forbids ALTER CHECK; the only path to fix a legacy
        # stage-CHECK constraint that lacks ``'not_selected'`` is the
        # rename-create-copy-drop dance. Idempotent: skip when the
        # constraint already includes it.
        _relax_jobs_stage_check_if_needed(conn)

    if _table_exists(conn, "cost_calibration"):
        conn.execute("DROP INDEX IF EXISTS idx_cost_calibration_polled_at")
        conn.execute("DROP TABLE cost_calibration")

    if _table_exists(conn, "onboarding_sessions"):
        sess_cols = _columns(conn, "onboarding_sessions")
        if "tester_openrouter_key" not in sess_cols:
            conn.execute("ALTER TABLE onboarding_sessions ADD COLUMN tester_openrouter_key TEXT DEFAULT NULL")
        if "tester_rapidapi_key" not in sess_cols:
            conn.execute("ALTER TABLE onboarding_sessions ADD COLUMN tester_rapidapi_key TEXT DEFAULT NULL")
        if "cumulative_cost_usd" not in sess_cols:
            conn.execute("ALTER TABLE onboarding_sessions ADD COLUMN cumulative_cost_usd REAL NOT NULL DEFAULT 0")
        if "tester_google_key" in sess_cols:
            conn.execute("ALTER TABLE onboarding_sessions DROP COLUMN tester_google_key")


def _extract_table_ddl(migration_filename: str, table: str) -> tuple[str, list[str]]:
    """Parse ``CREATE TABLE`` + every ``CREATE INDEX`` for a table out of a migration.

    Returns ``(create_table_sql, [create_index_sql, ...])`` with ``IF NOT
    EXISTS`` stripped — the rebuild path needs a fresh CREATE that would
    otherwise short-circuit because the renamed shell still exists in
    ``sqlite_master`` momentarily.

    Used by the constraint-rebuild helpers below. SQLite has no
    ``ALTER TABLE ... ALTER CHECK``, so the only way to change a CHECK
    constraint is rename-create-copy-drop. Both the table and its
    associated indexes must be re-created (indexes follow the renamed
    table on rename, then vanish when the renamed shell is dropped —
    verified empirically against sqlite_master).
    """
    sql = (MIGRATIONS_DIR / migration_filename).read_text(encoding="utf-8")
    create_re = re.compile(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \(.*?\n\);", re.DOTALL)
    create_match = create_re.search(sql)
    if create_match is None:  # pragma: no cover — caller asserts a valid (file, table)
        raise RuntimeError(f"Could not locate CREATE TABLE {table} in {migration_filename}")
    create_sql = create_match.group(0).replace("IF NOT EXISTS ", "")

    index_re = re.compile(
        rf"CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\S+\s+ON\s+{re.escape(table)}\s*\([^)]*\)(?:\s+WHERE\s+[^;]*)?;",
        re.IGNORECASE,
    )
    index_sqls = [m.group(0) for m in index_re.finditer(sql)]
    return create_sql, index_sqls


def _rebuild_table_with_indexes(
    conn: sqlite3.Connection,
    *,
    table: str,
    migration_filename: str,
    legacy_alias: str,
) -> None:
    """Rebuild ``table`` from its DDL in ``migration_filename``, preserving rows + indexes.

    The recipe: PRAGMA foreign_keys=OFF, rename original to ``legacy_alias``,
    run the migration's CREATE TABLE, INSERT INTO new SELECT FROM old over
    the column set the two schemas share, re-execute every CREATE INDEX from
    the migration and from any later migration that targets the same table,
    DROP the legacy shell.

    Column-set discipline: the source schema may carry drift columns the
    head schema no longer defines (see ``_DOCUMENTED_DEAD_COLUMNS`` in the
    test suite for the historical set). Copying those raises
    ``no such column`` mid-INSERT. The fix is to intersect ``legacy_cols``
    with the head schema's columns and only carry forward the overlap.
    Drift columns are dropped silently on the rebuild.

    Row-count invariant: assert ``new == legacy`` before dropping the
    legacy shell. Defense in depth against any future case where the
    INSERT silently under-copies (e.g. a CHECK violation on one row);
    without this, the rebuild can leave an empty new table next to a
    populated legacy shell. The on-disk evidence of #723 was exactly
    that shape.

    Atomicity: an explicit BEGIN wraps every DDL/DML statement so a
    failure in any step (rename / create / insert / drop / index)
    rolls back to the pre-rebuild state instead of leaving a renamed
    legacy + an empty new ``jobs`` for the next triage cycle to
    repopulate from scratch.

    Index re-creation is non-negotiable: ``DROP TABLE`` removes the named
    indexes that ``ALTER TABLE RENAME`` left attached to the renamed
    shell. Without re-applying them, the rebuilt table runs without
    ``idx_jobs_fingerprint`` / ``idx_jobs_stage`` / etc., silently
    degrading every dashboard query.

    Later-migration index forward-compatibility: the primary DDL lives in
    ``migration_filename`` (typically ``0001_initial.sql``), but later
    migrations may add more indexes on the same table via their own SQL
    files (e.g. ``idx_jobs_company_tier`` added in ``0007_tuning_loop_phase1.sql``).
    After the named-file indexes are applied, a second pass over all
    migration files (excluding ``migration_filename`` itself) gathers any
    additional ``CREATE INDEX ... ON {table}`` statements and executes them.
    All index statements carry ``IF NOT EXISTS`` so the pass is idempotent.
    """
    create_sql, index_sqls = _extract_table_ddl(migration_filename, table)

    # Read the stamped schema version so we know which later migrations have
    # already been applied to this DB. Columns + indexes from those migrations
    # must be re-applied after the rebuild; columns from migrations NOT YET
    # stamped must not be touched here (the migration runner will add them).
    _stamped_version: int = _read_schema_version(conn) or 0

    # Collect ADD COLUMN and index statements for this table from later migration files.
    # Applied in migration order so column additions precede their dependent indexes.
    _later_add_col_re = re.compile(
        rf"ALTER\s+TABLE\s+{re.escape(table)}\s+ADD\s+COLUMN\s+[^;]+;",
        re.IGNORECASE,
    )
    _later_index_re = re.compile(
        rf"CREATE\s+(?:UNIQUE\s+)?INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\S+\s+ON\s+{re.escape(table)}\s*\([^)]*\)(?:\s+WHERE\s+[^;]*)?;",
        re.IGNORECASE,
    )
    # Tuple of (migration_version, add_col_stmts, index_stmts) per migration file,
    # sorted by file name. Only files with version <= _stamped_version are eligible
    # (those migrations have already run; their schema changes must survive the rebuild).
    _later_col_and_idx: list[tuple[list[str], list[str]]] = []
    for _mig_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if _mig_path.name == migration_filename:
            continue  # already handled by _extract_table_ddl above
        _m = _FILENAME_RE.match(_mig_path.name)
        if not _m:
            continue
        _mig_version = int(_m.group(1))
        if _mig_version > _stamped_version:
            continue  # migration not yet applied — runner will handle it
        _sql = _mig_path.read_text(encoding="utf-8")
        _cols = [m.group(0) for m in _later_add_col_re.finditer(_sql)]
        _idxs = [m.group(0) for m in _later_index_re.finditer(_sql)]
        if _cols or _idxs:
            _later_col_and_idx.append((_cols, _idxs))

    legacy_cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        try:
            conn.execute(f"ALTER TABLE {table} RENAME TO {legacy_alias}")
            conn.execute(create_sql)
            new_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            copy_cols = [c for c in legacy_cols if c in new_cols]
            cols_csv = ",".join(copy_cols)
            conn.execute(f"INSERT INTO {table} ({cols_csv}) SELECT {cols_csv} FROM {legacy_alias}")
            legacy_count = conn.execute(f"SELECT COUNT(*) FROM {legacy_alias}").fetchone()[0]
            new_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if new_count != legacy_count:
                raise RuntimeError(f"row count mismatch rebuilding {table}: legacy={legacy_count}, new={new_count}")
            conn.execute(f"DROP TABLE {legacy_alias}")
            for index_sql in index_sqls:
                conn.execute(index_sql)
            # Re-apply ADD COLUMN + index statements from every migration that
            # has already been stamped (version <= _stamped_version). The rebuild
            # drops the table, losing those columns and indexes; we must restore
            # them so the post-rebuild schema matches what the migration runner
            # had previously established.
            # Guard: skip ADD COLUMN if the column already exists (handles the
            # case where the rebuild is somehow triggered multiple times, or
            # where a future migration both rebuilds and alters).
            for _add_cols, _idxs in _later_col_and_idx:
                present_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for add_col_sql in _add_cols:
                    _col_match = re.search(r"ADD\s+COLUMN\s+(\w+)", add_col_sql, re.IGNORECASE)
                    col_name = _col_match.group(1) if _col_match else None
                    if col_name and col_name in present_cols:
                        continue  # already present — idempotent skip
                    conn.execute(add_col_sql)
                for index_sql in _idxs:
                    conn.execute(index_sql)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _relax_jobs_stage_check_if_needed(conn: sqlite3.Connection) -> None:
    """If the legacy ``jobs.stage`` CHECK lacks ``'not_selected'``,
    rebuild the table with the v1 CHECK. Idempotent: a no-op when the
    CHECK already includes the value.
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    if schema_row is None:
        return
    current_sql = schema_row[0] or ""
    if "'not_selected'" in current_sql:
        return  # already at v1 shape
    _rebuild_table_with_indexes(
        conn,
        table="jobs",
        migration_filename="0001_initial.sql",
        legacy_alias="_jobs_legacy_pre_v1",
    )


def _add_briefing_ready_stage_if_needed(conn: sqlite3.Connection) -> None:
    """If the live ``jobs.stage`` CHECK lacks ``'briefing_ready'``,
    rebuild the table from 0001_initial.sql to pick up the new value.
    Idempotent: a no-op when the constraint already includes it.

    Hooked from :func:`apply_pending` so every connect picks up the
    constraint update without bumping ``_meta.schema_version`` (the
    change is a constraint relaxation, not a new column or table —
    invisible to schema_version readers).
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    if schema_row is None:
        return
    current_sql = schema_row[0] or ""
    if "'briefing_ready'" in current_sql:
        return  # already at #691 shape
    _rebuild_table_with_indexes(
        conn,
        table="jobs",
        migration_filename="0001_initial.sql",
        legacy_alias="_jobs_pre_briefing_ready",
    )


def _tighten_score_status_check_if_needed(conn: sqlite3.Connection) -> None:
    """If the live ``jobs.score_status`` CHECK still permits ``'needs_info'``,
    rebuild the table from 0001_initial.sql to drop it from the constraint.
    Idempotent: a no-op once the constraint is already tightened.

    Unlike the relaxation helpers above, this *tightens* the constraint.
    The rebuild's ``INSERT INTO new SELECT FROM legacy`` would itself
    fail with a CHECK violation if any row carried ``score_status =
    'needs_info'`` — but the error message at that point is generic. A
    pre-rebuild row-count probe lets us fail with a specific message
    (and before the rename + create do any work). Per #498 AC#2:
    "existing rows verified ``score_status != 'needs_info'`` before the
    constraint tightens."

    Verified at filing time that every active deployment carried
    zero ``needs_info`` rows. The guard is defense in depth against
    future drift, not a known-failure path.

    Hooked from :func:`apply_pending` so every connect picks up the
    tightening without bumping ``_meta.schema_version`` (constraint-only
    change, invisible to schema_version readers).

    Coverage note: the guard runs first among the post-migration
    helpers, but ``_bridge_legacy_to_v1`` runs earlier still — its
    ``_relax_jobs_stage_check_if_needed`` rebuilds the jobs table from
    the (now-tightened) 0001 CHECK and would surface a generic
    ``sqlite3.IntegrityError`` on a v0.10-shape stack that carried
    ``needs_info`` rows. Cold path: every active deployment is at
    ``schema_version=1`` already, so the bridge is unreachable. If a
    future fresh v0.10 import needs the specific message, move the
    guard ahead of :func:`_infer_baseline_version`.
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    if schema_row is None:
        return
    current_sql = schema_row[0] or ""
    if "'needs_info'" not in current_sql:
        return  # already at #498 shape
    legacy_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE score_status='needs_info'").fetchone()[0]
    if legacy_count > 0:
        raise RuntimeError(
            f"refusing to tighten jobs.score_status CHECK: {legacy_count} row(s) carry score_status='needs_info'. "
            "Resolve these rows (re-score or update to 'scored'/'manual_review') before restarting; see #498."
        )
    _rebuild_table_with_indexes(
        conn,
        table="jobs",
        migration_filename="0001_initial.sql",
        legacy_alias="_jobs_pre_score_status_tighten",
    )


def _add_prep_phase_b_kind_if_needed(conn: sqlite3.Connection) -> None:
    """If the live ``background_tasks.kind`` CHECK lacks ``'prep_phase_b'``,
    rebuild from 0002_background_tasks.sql to pick up the new value.
    Idempotent: a no-op when the constraint already includes it.

    Companion to :func:`_add_briefing_ready_stage_if_needed` (#691). The
    new Phase B subprocess registers a row with ``kind='prep_phase_b'``
    so the watchdog can reap stuck Phase B runs into ``briefing_ready``
    (preserving the briefing folder) instead of ``scored`` (the
    legacy reset target for ``kind='prep'``).
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='background_tasks'").fetchone()
    if schema_row is None:
        return  # background_tasks table not yet created (pre-0002 stack)
    current_sql = schema_row[0] or ""
    if "'prep_phase_b'" in current_sql:
        return  # already at #691 shape
    _rebuild_table_with_indexes(
        conn,
        table="background_tasks",
        migration_filename="0002_background_tasks.sql",
        legacy_alias="_background_tasks_pre_phase_b",
    )


def _add_podcast_kind_if_needed(conn: sqlite3.Connection) -> None:
    """If the live ``background_tasks.kind`` CHECK lacks ``'podcast'``,
    rebuild from 0002_background_tasks.sql to pick up the new value.
    Idempotent: a no-op when the constraint already includes it.

    #879: server-side progress tracking for synchronous podcast generation
    via the ``background_tasks`` table (``writeback_sync`` pattern).
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='background_tasks'").fetchone()
    if schema_row is None:
        return
    current_sql = schema_row[0] or ""
    if "'podcast'" in current_sql:
        return
    _rebuild_table_with_indexes(
        conn,
        table="background_tasks",
        migration_filename="0002_background_tasks.sql",
        legacy_alias="_background_tasks_pre_podcast",
    )


def _add_withdrawn_fallback_stage_if_needed(conn: sqlite3.Connection) -> None:
    """If the live ``jobs.stage`` CHECK lacks ``'withdrawn_fallback'``,
    rebuild the table from 0001_initial.sql to pick up the new value.
    Idempotent: a no-op when the constraint already includes it.

    #358: fallback queue for withdrew-but-might-revisit jobs.
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    if schema_row is None:
        return
    current_sql = schema_row[0] or ""
    if "'withdrawn_fallback'" in current_sql:
        return
    _rebuild_table_with_indexes(
        conn,
        table="jobs",
        migration_filename="0001_initial.sql",
        legacy_alias="_jobs_pre_withdrawn_fallback",
    )


def _add_fallback_tab_to_view_prefs_if_needed(conn: sqlite3.Connection) -> None:
    """If the live ``view_prefs.tab`` CHECK lacks ``'fallback'``,
    rebuild from 0005_view_prefs.sql. Idempotent.

    #358: fallback queue needs its own tab in the view_prefs table.
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='view_prefs'").fetchone()
    if schema_row is None:
        return
    current_sql = schema_row[0] or ""
    if "'fallback'" in current_sql:
        return
    _rebuild_table_with_indexes(
        conn,
        table="view_prefs",
        migration_filename="0005_view_prefs.sql",
        legacy_alias="_view_prefs_pre_fallback",
    )


def _infer_baseline_version(conn: sqlite3.Connection) -> int:
    """Infer the starting schema_version when no ``_meta`` row exists.

    Returns:
        - ``0`` if the DB has no ``jobs`` table — a fresh install. The
          apply pass will run 0001 from scratch.
        - ``1`` if the DB is already at equilibrium — every active
          deployment circa 2026-05-08. No DDL runs.
        - ``0`` after running :func:`_bridge_legacy_to_v1` for legacy
          drift. The bridge fixes existing-table column drift but
          doesn't create missing tables (e.g. ``notifications`` is
          absent on v0.10). Returning 0 lets the apply pass run 0001 —
          its ``CREATE TABLE IF NOT EXISTS`` statements fill in missing
          tables without disturbing what the bridge already aligned.
    """
    if not _table_exists(conn, "jobs"):
        return 0
    if _is_equilibrium(conn):
        return 1
    _bridge_legacy_to_v1(conn)
    return 0


def _list_migrations() -> list[tuple[int, str, Path]]:
    """Discover migration files. Returns ``[(version, slug, path), ...]`` sorted by version.

    Files that don't match ``NNNN_slug.sql`` are silently ignored — keep
    READMEs and notes in the directory without breaking discovery.
    """
    if not MIGRATIONS_DIR.is_dir():
        return []
    found: list[tuple[int, str, Path]] = []
    for entry in MIGRATIONS_DIR.iterdir():
        if not entry.is_file():
            continue
        m = _FILENAME_RE.match(entry.name)
        if not m:
            continue
        version = int(m.group(1))
        slug = entry.stem.split("_", 1)[1]
        found.append((version, slug, entry))
    found.sort(key=lambda t: t[0])
    return found


def apply_pending(conn: sqlite3.Connection, *, dry_run: bool = False) -> list[AppliedMigration]:
    """Bring the connected DB up to the head migration version.

    Args:
        conn: Open SQLite connection. The runner uses ``conn`` for both
            schema reads and DDL writes; pass a writable connection.
        dry_run: When True, walks the work that would be done without
            mutating the DB. Returns the same shape with ``skipped=True``
            on each entry.

    Returns:
        List of :class:`AppliedMigration` records describing every
        migration considered. Empty list when the DB is already at the
        head version (the common case after deployments update).

    Raises:
        sqlite3.OperationalError: any error inside a migration's
            transaction; the migration's transaction is rolled back, no
            ``schema_version`` write occurs.
    """
    applied: list[AppliedMigration] = []

    if not dry_run:
        _ensure_meta_table(conn)
        conn.commit()

    # Read version. ``_meta`` may be absent under dry_run (we don't
    # create it) or absent on a fresh DB before the heuristic runs.
    if _table_exists(conn, "_meta"):
        current = _read_schema_version(conn)
    else:
        current = None

    if current is None:
        if dry_run:
            # Predict what the heuristic would record without mutating.
            # Equilibrium → 1, otherwise (fresh OR legacy) → 0.
            current = 1 if _table_exists(conn, "jobs") and _is_equilibrium(conn) else 0
        else:
            current = _infer_baseline_version(conn)
            _write_schema_version(conn, current)
            conn.commit()

    for version, slug, path in _list_migrations():
        if version <= current:
            continue
        if dry_run:
            applied.append(AppliedMigration(version=version, name=slug, path=path, skipped=True))
            continue
        sql = path.read_text(encoding="utf-8")
        # ``executescript`` issues an implicit COMMIT before running, so a
        # hand-rolled ``conn.execute("BEGIN")`` is committed away to nothing
        # and the statements then run in autocommit — a partial apply cannot
        # be rolled back. The fix is to put transaction control *inside* the
        # script ``executescript`` runs, where SQLite honors it. The
        # ``schema_version`` stamp is folded into the same transaction so the
        # version bump and the migration's statements commit (or roll back)
        # as one unit — no window where the schema advanced but the version
        # row didn't. On failure the transaction is left open; ``rollback``
        # discards every statement, including the version stamp.
        script = (
            "BEGIN;\n"
            f"{sql}\n"
            "INSERT INTO _meta(key, value) "
            f"VALUES('schema_version', '{int(version)}') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value;\n"
            "COMMIT;\n"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            conn.rollback()
            raise
        applied.append(AppliedMigration(version=version, name=slug, path=path, skipped=False))
        current = version

    # Constraint-only relaxations that can't bump schema_version (no new
    # tables or columns to track). Each helper is idempotent — short-
    # circuits on a no-op probe of the live schema. Hook order matters
    # only when one helper depends on another; today they don't.
    if not dry_run:
        # Tightening helper runs first: its specific row-count guard
        # must fire before any other helper triggers a generic rebuild
        # that would surface a buried CHECK-violation IntegrityError.
        _tighten_score_status_check_if_needed(conn)
        _add_briefing_ready_stage_if_needed(conn)
        _add_prep_phase_b_kind_if_needed(conn)
        _add_podcast_kind_if_needed(conn)
        _add_withdrawn_fallback_stage_if_needed(conn)
        _add_fallback_tab_to_view_prefs_if_needed(conn)

    return applied
