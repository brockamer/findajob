#!/usr/bin/env python3
"""Stale-task cleanup watchdog.

Runs every 10 min via supercronic. Three responsibilities, all driven by the
``background_tasks`` table after M6:

1. **Reap stuck `prep` rows.** Any ``background_tasks`` row with
   ``kind='prep'`` and ``status='running'`` older than 60 minutes is marked
   ``failed``; the corresponding ``jobs`` row is rolled back from
   ``prep_in_progress`` to ``scored`` via :func:`findajob.actions.reset_prep_to_scored`
   so the operator can re-flag.
2. **Reap stuck `interview_prep` and `speculative_research` rows.** Same
   pattern; per-kind timeouts from :data:`findajob.background_tasks.KIND_TIMEOUT_MINUTES`.
   Speculative research stamps the corresponding ``speculative_requests``
   row to ``status='failed'`` so the operator's status page stops polling.
3. **Sweep orphan folders.** Top-level companies/ subdirectories that no
   jobs row references and that are older than ``ORPHAN_FOLDER_MIN_AGE_MIN``
   move to ``companies/.stale/`` for forensic inspection.

Pre-M6 this file did stage-time heuristics on ``jobs.stage_updated``;
M6 swap moved the signal into ``background_tasks`` rows, which gives a
faster + more accurate signal (the row exists from the moment the
launcher inserts it, before ``stage_updated`` writes happen).
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from findajob.actions import (
    reset_briefing_ready_to_scored,
    reset_prep_to_briefing_ready,
    reset_prep_to_scored,
)
from findajob.audit import log_event
from findajob.background_tasks import KIND_TIMEOUT_MINUTES, find_stuck, record_failed
from findajob.db import connect
from findajob.paths import BASE

DB_PATH = f"{BASE}/data/pipeline.db"
ORPHAN_FOLDER_MIN_AGE_MIN = 120
"""2h grace before sweeping an orphan — long enough that an in-flight prep
that hasn't yet written prep_folder_path won't get swept mid-run."""

BRIEFING_READY_STALE_AGE_HOURS = 48
"""Briefing-ready jobs the operator hasn't decided on within 48h reset to
``scored``. Keeps the dashboard's awaiting-decision section honest while
preserving ``prep_folder_path`` so a re-flag resurfaces the existing
briefing rather than re-paying Phase A (#691)."""

_WATCHDOG_REASON = "watchdog_stale_reset"
_BRIEFING_STALE_REASON = "watchdog_briefing_ready_stale"


def reap_prep(conn: sqlite3.Connection) -> int:
    """Mark stuck `prep` background_tasks failed; reset jobs.stage to scored.

    Stage reset routes through :func:`findajob.actions.reset_prep_to_scored`
    so the audit_log entry + folder cleanup are consistent with the
    user-driven path. Watchdog never bypasses the action layer.
    """
    timeout = KIND_TIMEOUT_MINUTES["prep"]
    stuck = find_stuck(conn, kind="prep", max_age_minutes=timeout)
    count = 0
    for row in stuck:
        record_failed(
            conn,
            row["id"],
            error_message=f"watchdog: subprocess > {timeout}min — likely died (PID {row['pid']})",
        )
        if reset_prep_to_scored(conn, row["job_id"], reason=_WATCHDOG_REASON):
            count += 1
    return count


def reap_prep_phase_b(conn: sqlite3.Connection) -> int:
    """Mark stuck `prep_phase_b` background_tasks failed; reset stage to briefing_ready.

    Companion to :func:`reap_prep` for the #691 briefing-first gate. The
    distinct ``kind='prep_phase_b'`` (vs ``kind='prep'``) lets the
    watchdog route Phase B failures to ``reset_prep_to_briefing_ready``
    (preserves ``prep_folder_path`` + briefing folder) instead of
    ``reset_prep_to_scored`` (would discard both). The operator can
    re-try Phase B without re-paying Phase A.
    """
    timeout = KIND_TIMEOUT_MINUTES["prep_phase_b"]
    stuck = find_stuck(conn, kind="prep_phase_b", max_age_minutes=timeout)
    count = 0
    for row in stuck:
        record_failed(
            conn,
            row["id"],
            error_message=f"watchdog: subprocess > {timeout}min — likely died (PID {row['pid']})",
        )
        if reset_prep_to_briefing_ready(conn, row["job_id"], reason=_WATCHDOG_REASON):
            count += 1
    return count


def reap_briefing_ready_stale(conn: sqlite3.Connection) -> int:
    """Reset briefing_ready jobs older than 48h to ``scored``.

    The 48h ceiling on the operator decision window (#691). A briefing
    the operator never decided on isn't dead — ``prep_folder_path`` is
    preserved so a re-flag resurfaces the existing briefing rather than
    re-paying Phase A. This reaper cleans up the dashboard's
    awaiting-decision section without forfeiting Phase A's work.

    Distinct from :func:`reap_prep_phase_b`: this one is time-since-stage,
    not subprocess-stuck. No ``background_tasks`` row to mark failed —
    Phase A already finished cleanly; only the operator's decision is
    overdue. Uses ``jobs.stage_updated`` as the age signal.
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=BRIEFING_READY_STALE_AGE_HOURS)).isoformat()
    stale = conn.execute(
        "SELECT id FROM jobs WHERE stage='briefing_ready' AND stage_updated < ?",
        (cutoff,),
    ).fetchall()
    count = 0
    for row in stale:
        if reset_briefing_ready_to_scored(conn, row["id"], reason=_BRIEFING_STALE_REASON):
            count += 1
    return count


def reap_interview_prep(conn: sqlite3.Connection) -> int:
    """Mark stuck `interview_prep` background_tasks failed.

    No stage reset — interview_prep doesn't move ``jobs.stage`` to a
    transient state the way prep does (`prep_in_progress`). The job
    remains in `interview` regardless of whether the artifact was
    generated. Marking the row failed surfaces the failure on the
    operator's status page.
    """
    timeout = KIND_TIMEOUT_MINUTES["interview_prep"]
    stuck = find_stuck(conn, kind="interview_prep", max_age_minutes=timeout)
    count = 0
    for row in stuck:
        record_failed(
            conn,
            row["id"],
            error_message=f"watchdog: subprocess > {timeout}min — likely died (PID {row['pid']})",
        )
        log_event("interview_prep_watchdog_failed", task_id=row["id"], job_id=row["job_id"])
        count += 1
    return count


def reap_speculative_research(conn: sqlite3.Connection) -> int:
    """Mark stuck `speculative_research` rows failed; stamp speculative_requests.status='failed'.

    Two-surface update: ``background_tasks`` for the M6 audit trail,
    and ``speculative_requests`` for the existing status-page UI. The
    legacy ``fail_stuck_speculative`` heuristic this replaces only
    touched ``speculative_requests`` — losing visibility into "which
    PID? which start time?" Watchdog now writes both.
    """
    timeout = KIND_TIMEOUT_MINUTES["speculative_research"]
    stuck = find_stuck(conn, kind="speculative_research", max_age_minutes=timeout)
    count = 0
    for row in stuck:
        record_failed(
            conn,
            row["id"],
            error_message=f"watchdog: subprocess > {timeout}min — likely died (PID {row['pid']})",
        )
        # job_id stores the speculative_requests.id stringified.
        try:
            request_id = int(row["job_id"])
        except (TypeError, ValueError):
            log_event("speculative_research_watchdog_skip", task_id=row["id"], reason="invalid_job_id")
            continue
        try:
            conn.execute(
                """UPDATE speculative_requests
                   SET status='failed', error_message=?
                   WHERE id=? AND status='researching'""",
                (f"research timed out (>{timeout}min) — subprocess likely died", request_id),
            )
            conn.commit()
        except sqlite3.OperationalError:
            # Speculative table missing on a legacy stack — record the
            # background_tasks failure but skip the parallel update.
            pass
        log_event("speculative_research_watchdog_failed", task_id=row["id"], request_id=request_id)
        count += 1
    return count


def sweep_orphan_folders(conn: sqlite3.Connection) -> int:
    """Move orphan top-level folders in companies/ to companies/.stale/.

    An orphan is a top-level subdirectory (not starting with `_` or `.`) that
    no `jobs` row's `prep_folder_path` references AND whose mtime is older
    than ORPHAN_FOLDER_MIN_AGE_MIN. The age guard prevents sweeping a folder
    mid-prep — a slow in-flight run hasn't yet written prep_folder_path.

    Caused-by paths covered:
    - prep orchestrator exception handler nulls prep_folder_path but doesn't
      shutil.rmtree the partial folder.
    - reset_prep_to_scored() nulls prep_folder_path because at reset time
      it doesn't have outdir info.
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
        prep_count = reap_prep(conn)
        phase_b_count = reap_prep_phase_b(conn)
        briefing_stale_count = reap_briefing_ready_stale(conn)
        interview_count = reap_interview_prep(conn)
        spec_count = reap_speculative_research(conn)
        orphans = sweep_orphan_folders(conn)
    finally:
        conn.close()
    log_event(
        "watchdog_run",
        stale_reset=prep_count,
        prep_phase_b_failed=phase_b_count,
        briefing_ready_stale_reset=briefing_stale_count,
        interview_failed=interview_count,
        speculative_failed=spec_count,
        orphans_swept=orphans,
    )


if __name__ == "__main__":
    main()
