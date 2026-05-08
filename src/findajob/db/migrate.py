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
3. Apply every numbered migration with version > current. Each runs in
   its own transaction; the version row is updated within the same
   transaction so a half-applied migration cannot leave the DB at the
   later version.

The heuristic exists because real production stacks have no ``_meta``
table on first contact with this runner. The cohort wave (M5.E2) bumps
every active stack to the M5-shipping minor; first boot under that
image hits the heuristic exactly once, then ``_meta.schema_version=1``
and all subsequent boots are no-ops.

Migration files live in ``$BASE/migrations/{NNNN}_{slug}.sql``. Numbering
starts at 0001. The scheme is intentionally append-only: never rename or
edit an applied migration; ship a new numbered file instead.
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
    "tester_openrouter_key",
    "tester_rapidapi_key",
    "cumulative_cost_usd",
)
_REMOVED_ONBOARDING_SESSIONS_COLUMNS: tuple[str, ...] = ("tester_google_key",)
_REQUIRED_TABLES: tuple[str, ...] = ("notifications",)
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


def _relax_jobs_stage_check_if_needed(conn: sqlite3.Connection) -> None:
    """If the legacy ``jobs.stage`` CHECK lacks ``'not_selected'``,
    rebuild the table with the v1 CHECK. Idempotent: a no-op when the
    CHECK already includes the value.

    SQLite has no ``ALTER TABLE ... ALTER CHECK`` — the only way to
    change a CHECK constraint is to recreate the table. The recipe:
    rename the original out of the way, run 0001's CREATE TABLE jobs
    DDL, copy rows by intersecting column sets, drop the old table.
    """
    schema_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    if schema_row is None:
        return
    current_sql = schema_row[0] or ""
    if "'not_selected'" in current_sql:
        return  # already at v1 shape

    # Read the canonical CREATE TABLE jobs block from 0001.
    initial_sql = (MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8")
    create_match = re.search(r"(CREATE TABLE IF NOT EXISTS jobs \(.*?\n\);)", initial_sql, re.DOTALL)
    if create_match is None:  # pragma: no cover — 0001 always has this block
        raise RuntimeError("Could not locate CREATE TABLE jobs in 0001_initial.sql")
    new_create = create_match.group(1).replace("IF NOT EXISTS jobs", "jobs")

    # Capture the legacy column list so the INSERT INTO ... SELECT
    # only references columns the legacy table actually has — never
    # the v1 columns added by ALTER TABLE earlier in this bridge.
    legacy_cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    cols_csv = ",".join(legacy_cols)

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("ALTER TABLE jobs RENAME TO _jobs_legacy_pre_v1")
        conn.executescript(new_create)
        conn.execute(f"INSERT INTO jobs ({cols_csv}) SELECT {cols_csv} FROM _jobs_legacy_pre_v1")
        conn.execute("DROP TABLE _jobs_legacy_pre_v1")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _infer_baseline_version(conn: sqlite3.Connection) -> int:
    """Infer the starting schema_version when no ``_meta`` row exists.

    Returns:
        - ``0`` if the DB has no ``jobs`` table — a fresh install. The
          apply pass will run 0001 from scratch.
        - ``1`` if the DB is already at equilibrium — every active
          tester / operator stack circa 2026-05-08. No DDL runs.
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
        head version (the common case after the cohort wave settles).

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
        try:
            conn.execute("BEGIN")
            conn.executescript(sql)
            _write_schema_version(conn, version)
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        applied.append(AppliedMigration(version=version, name=slug, path=path, skipped=False))
        current = version

    return applied
