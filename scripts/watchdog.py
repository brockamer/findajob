#!/usr/bin/env python3
"""Stale-prep cleanup watchdog.

Runs every 10 min via supercronic. Three responsibilities:

1. Reset jobs stuck in stage='prep_in_progress' > STALE_PREP_MINUTES back
   to 'scored' so the operator can re-flag them. A subprocess crash
   (container restart, OOM, timeout) leaves the stage stuck; this is the
   safety net.
2. Fail speculative_requests rows stuck in 'researching' > STALE_RESEARCH_MINUTES
   so the operator's status page stops polling forever.
3. Sweep orphan folders in companies/ that have no matching jobs row pointing
   at them and are older than ORPHAN_FOLDER_MIN_AGE_MIN. Moves them to
   companies/.stale/ for forensic inspection rather than deleting.
   Catches any code path that creates a folder but fails to write the
   prep_folder_path back to DB (in-process exception, OOM during prep, etc.).

Replaces scripts/poll_flags.py — all transition logic now lives in the web
POST handlers (findajob.web.routes.board_actions) calling findajob.actions.
"""

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from findajob.actions import reset_prep_to_scored
from findajob.db import connect
from findajob.paths import BASE
from findajob.utils import log_event

DB_PATH = f"{BASE}/data/pipeline.db"
STALE_PREP_MINUTES = 60
STALE_RESEARCH_MINUTES = 15
ORPHAN_FOLDER_MIN_AGE_MIN = 120
"""2h grace before sweeping an orphan — long enough that an in-flight prep
that hasn't yet written prep_folder_path won't get swept mid-run."""


def run_watchdog(conn: sqlite3.Connection) -> int:
    """Reset any job stuck in prep_in_progress > STALE_PREP_MINUTES. Returns reset count.

    stage_updated is written by web handlers as Python's datetime.isoformat()
    (e.g. "2026-04-23T17:19:57.663641+00:00"). SQLite's datetime('now', ...) returns
    the naïve space-separated form ("2026-04-23 16:19:57"); lexical `<` against
    an ISO-T value is unreliable on same-day rows because `T` > space at pos 10.
    Compute the cutoff in Python as an ISO string so both sides share the format.
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=STALE_PREP_MINUTES)).isoformat()
    stale = conn.execute(
        """SELECT id FROM jobs
           WHERE stage = 'prep_in_progress'
             AND stage_updated < ?""",
        (cutoff,),
    ).fetchall()
    count = 0
    for job in stale:
        if reset_prep_to_scored(conn, job["id"], reason="watchdog_stale_reset"):
            count += 1
    return count


def fail_stuck_speculative(conn: sqlite3.Connection) -> int:
    """Mark speculative_requests rows stuck in 'researching' > STALE_RESEARCH_MINUTES as failed.

    Covers the silent-hang case where the detached run_speculative_research.py
    subprocess died (OOM, container restart) without updating the row. Without
    this the operator's status page polls forever.

    submitted_at uses SQLite's datetime('now') format (naïve space-separated)
    via the column DEFAULT. Cutoff comparison uses the same format so the lex
    `<` comparison is reliable.
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=STALE_RESEARCH_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        stuck = conn.execute(
            """SELECT id, company FROM speculative_requests
               WHERE status = 'researching'
                 AND submitted_at < ?""",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table absent (legacy stack pre-B1 migration). Gracefully skip.
        return 0
    count = 0
    for sr in stuck:
        conn.execute(
            """UPDATE speculative_requests
               SET status='failed',
                   error_message=?
               WHERE id=?""",
            (f"research timed out (>{STALE_RESEARCH_MINUTES} min) — subprocess likely died", sr["id"]),
        )
        log_event(
            "speculative_research_watchdog_failed",
            request_id=sr["id"],
            company=sr["company"],
        )
        count += 1
    if count:
        conn.commit()
    return count


def sweep_orphan_folders(conn: sqlite3.Connection) -> int:
    """Move orphan top-level folders in companies/ to companies/.stale/.

    An orphan is a top-level subdirectory (not starting with `_` or `.`) that
    no `jobs` row's `prep_folder_path` references AND whose mtime is older
    than ORPHAN_FOLDER_MIN_AGE_MIN. The age guard prevents sweeping a folder
    mid-prep — a slow in-flight run hasn't yet written prep_folder_path.

    Caused-by paths covered:
    - prep_application.py exception handler nulls prep_folder_path but doesn't
      shutil.rmtree the partial folder.
    - watchdog's reset_prep_to_scored() nulls prep_folder_path because at
      reset time it doesn't have outdir info.
    - Container kill / OOM during prep — process never wrote prep_folder_path
      to DB.

    Returns count of folders moved.
    """
    companies_dir = Path(BASE) / "companies"
    if not companies_dir.is_dir():
        return 0

    db_paths = {
        r[0]
        for r in conn.execute(
            "SELECT prep_folder_path FROM jobs WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''"
        ).fetchall()
    }

    cutoff_ts = (datetime.now(UTC) - timedelta(minutes=ORPHAN_FOLDER_MIN_AGE_MIN)).timestamp()
    stale_dir = companies_dir / ".stale"

    count = 0
    for entry in companies_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith(("_", ".")):
            continue
        # DB stores paths as `/app/companies/<name>` (per BASE inside container)
        if str(entry) in db_paths:
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff_ts:
            continue  # too fresh — possibly an in-flight prep
        # Move to .stale/. mkdir is idempotent.
        stale_dir.mkdir(exist_ok=True)
        dst = stale_dir / entry.name
        if dst.exists():
            # Defensive: don't clobber an earlier sweep's same-named entry.
            log_event("orphan_folder_sweep_skipped", folder=entry.name, reason="dst_exists")
            continue
        try:
            shutil.move(str(entry), str(dst))
        except OSError as e:
            log_event("orphan_folder_sweep_failed", folder=entry.name, error=str(e))
            continue
        log_event("orphan_folder_swept", folder=entry.name)
        count += 1
    return count


def main() -> None:
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        count = run_watchdog(conn)
        spec_failed = fail_stuck_speculative(conn)
        orphans = sweep_orphan_folders(conn)
    finally:
        conn.close()
    log_event(
        "watchdog_run",
        stale_reset=count,
        speculative_failed=spec_failed,
        orphans_swept=orphans,
    )


if __name__ == "__main__":
    main()
