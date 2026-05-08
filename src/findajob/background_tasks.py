"""Helpers for the ``background_tasks`` table (M6.E1).

One row per detached subprocess run — `prep`, `interview_prep`, or
`speculative_research`. The launcher (an action handler / web route)
inserts a row with ``status='running'`` *before* spawning the
subprocess. The subprocess opens its own DB connection at the start
and on exit (via :func:`writeback_subprocess` context manager) writes
back ``status='succeeded'`` or ``'failed'``. SIGKILL paths leave the
row at ``running`` — the watchdog reaps via per-kind timeouts.

The launcher passes the new row's ``id`` to the subprocess via the
``FINDAJOB_BG_TASK_ID`` environment variable. The subprocess reads it
in :func:`task_id_from_env` and uses :func:`writeback_subprocess` to
close the row.

Why a helper module rather than inline SQL: every call site needs the
same operations, and the watchdog query has a specific shape that
benefits from being named. The orchestrator-side
:func:`writeback_subprocess` context manager folds the
read-env-then-record pattern into a single ``with`` statement.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

# Per-kind timeout in minutes. Watchdog reaps rows older than this and
# in ``status='running'``. Numbers chosen to match prior heuristics:
# - prep: 60min, matched by stale-prep watchdog reset (CLAUDE.md)
# - interview_prep: 30min, conservative ceiling for a one-shot Opus call
# - speculative_research: 10min, matched by ``fail_stuck_speculative``
# These are upper bounds — typical runtime is well below.
KIND_TIMEOUT_MINUTES: dict[str, int] = {
    "prep": 60,
    "interview_prep": 30,
    "speculative_research": 10,
}

# Environment variable through which the launcher passes the task_id
# to the spawned subprocess.
TASK_ID_ENV_VAR: str = "FINDAJOB_BG_TASK_ID"


def task_id_from_env() -> int | None:
    """Read the task id the launcher set via :data:`TASK_ID_ENV_VAR`.

    Returns ``None`` when the script is invoked outside a launcher
    context (manual CLI run, test harness without env). Subprocess
    writeback is best-effort: if the launcher didn't set the env, the
    subprocess simply does not call :func:`record_succeeded` /
    :func:`record_failed`. Watchdog still owns recovery.
    """
    raw = os.environ.get(TASK_ID_ENV_VAR)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def record_start(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    kind: str,
    pid: int | None = None,
) -> int:
    """Insert a new row in ``status='running'`` with ``started_at=now``.

    Args:
        conn: Open writable SQLite connection.
        job_id: ``jobs.id`` for prep/interview_prep; stringified
            ``speculative_requests.id`` for speculative_research.
        kind: One of the values constrained by the table's CHECK clause.
        pid: Subprocess PID when known. Optional — useful for forensic
            inspection ("which OS process owned this row?").

    Returns:
        The newly-inserted ``background_tasks.id``. The launcher passes
        this to the subprocess via :data:`TASK_ID_ENV_VAR`.
    """
    cur = conn.execute(
        "INSERT INTO background_tasks (job_id, kind, pid) VALUES (?, ?, ?)",
        (job_id, kind, pid),
    )
    conn.commit()
    task_id = cur.lastrowid
    if task_id is None:  # pragma: no cover — sqlite3 always returns id on AUTOINCREMENT INSERT
        raise RuntimeError("INSERT into background_tasks did not return a lastrowid")
    return task_id


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def record_succeeded(conn: sqlite3.Connection, task_id: int) -> None:
    """Mark ``task_id`` as succeeded. Idempotent on already-finished rows."""
    conn.execute(
        "UPDATE background_tasks SET status='succeeded', finished_at=? WHERE id=? AND status='running'",
        (_utcnow_iso(), task_id),
    )
    conn.commit()


def record_failed(conn: sqlite3.Connection, task_id: int, error_message: str) -> None:
    """Mark ``task_id`` as failed with the given error message.

    Truncates ``error_message`` to 4000 characters defensively — the
    column has no length constraint but a 50 KB Python traceback in a
    DB row helps no one and makes the audit page slow.
    """
    truncated = (error_message or "")[:4000]
    conn.execute(
        "UPDATE background_tasks SET status='failed', finished_at=?, error_message=? WHERE id=? AND status='running'",
        (_utcnow_iso(), truncated, task_id),
    )
    conn.commit()


def fetch_by_id(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    """Get a single row by id. Used by status-page polls."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT id, job_id, kind, started_at, finished_at, status, error_message, pid FROM background_tasks WHERE id=?",
        (task_id,),
    ).fetchone()


def find_stuck(conn: sqlite3.Connection, kind: str, max_age_minutes: int) -> list[sqlite3.Row]:
    """Return ``running`` rows of ``kind`` older than ``max_age_minutes``.

    Used by the watchdog. Cutoff is computed against UTC ``now``.
    Returned rows are :class:`sqlite3.Row` so callers can read columns
    by name.
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=max_age_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            "SELECT id, job_id, kind, started_at, finished_at, status, error_message, pid "
            "FROM background_tasks "
            "WHERE status='running' AND kind=? AND started_at < ? "
            "ORDER BY started_at ASC",
            (kind, cutoff),
        ).fetchall()
    )


def find_active_for_subject(conn: sqlite3.Connection, job_id: str, kind: str) -> sqlite3.Row | None:
    """Return the most-recent ``running`` row for ``(job_id, kind)`` or None.

    Used by status pages: "is there a task in flight for this subject?"
    Returns the most-recent row by ``started_at`` so a re-click that
    spawned a new task surfaces the new one.
    """
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT id, job_id, kind, started_at, finished_at, status, error_message, pid "
        "FROM background_tasks "
        "WHERE job_id=? AND kind=? AND status='running' "
        "ORDER BY started_at DESC LIMIT 1",
        (job_id, kind),
    ).fetchone()


@contextmanager
def writeback_subprocess(db_path: str) -> Iterator[int | None]:
    """Context manager: read task_id from env, record success/failure on exit.

    Used in the three orchestrator ``main()`` functions::

        def main() -> None:
            with writeback_subprocess(DB_PATH):
                ...do the work...

    On clean exit → :func:`record_succeeded`. On exception →
    :func:`record_failed` and re-raise. Both writebacks open a fresh
    short-timeout DB connection so the contract is independent of any
    connection state the orchestrator may have left behind. The
    exception still propagates so the subprocess exit code is
    preserved.

    Yields the task_id (or ``None`` when the env var isn't set —
    manual CLI run / test harness). Callers can use the yielded value
    for log_event correlation if they want.

    No-op when ``FINDAJOB_BG_TASK_ID`` isn't set: the orchestrator
    runs as before. Watchdog still owns recovery for SIGKILL.
    """
    # Local import: avoids a module-level circular when findajob.db
    # eventually imports findajob.background_tasks. It doesn't today,
    # but the local import costs nothing and forecloses the surface.
    from findajob.db import connect

    task_id = task_id_from_env()
    try:
        yield task_id
    except BaseException as exc:
        if task_id is not None:
            try:
                c = connect(db_path, timeout=5.0)
                try:
                    record_failed(c, task_id, error_message=f"{type(exc).__name__}: {exc}")
                finally:
                    c.close()
            except Exception:  # pragma: no cover — writeback is best-effort
                pass
        raise
    if task_id is not None:
        try:
            c = connect(db_path, timeout=5.0)
            try:
                record_succeeded(c, task_id)
            finally:
                c.close()
        except Exception:  # pragma: no cover — writeback is best-effort
            pass
