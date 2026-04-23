#!/usr/bin/env python3
"""Stale-prep cleanup watchdog.

Runs every 10 min via supercronic. Single responsibility: reset jobs stuck in
stage='prep_in_progress' for more than STALE_PREP_MINUTES back to 'scored'
so the operator can re-flag them. A subprocess crash (container restart, OOM,
timeout) leaves the stage stuck; this is the safety net.

Replaces scripts/poll_flags.py — all transition logic now lives in the web
POST handlers (findajob.web.routes.board_actions) calling findajob.actions.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

from findajob.actions import reset_prep_to_scored
from findajob.paths import BASE
from findajob.utils import log_event

DB_PATH = f"{BASE}/data/pipeline.db"
STALE_PREP_MINUTES = 60


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


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        count = run_watchdog(conn)
    finally:
        conn.close()
    log_event("watchdog_run", stale_reset=count)


if __name__ == "__main__":
    main()
