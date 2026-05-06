"""SQL helpers for #87 cost-visibility surfaces.

Pure functions over a sqlite3.Connection. No HTTP, no env reads, no
side effects beyond the SELECTs they execute. UI routes and notify.py
both consume this module so the calibration math lives in one place.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Stale threshold for the latest calibration row. Beyond this, surfaces
# fall back to uncalibrated rendering and badge the data as stale.
STALE_AFTER = timedelta(hours=1)


def _get(row: sqlite3.Row | tuple, idx: int, name: str):
    """Defensive accessor for both sqlite3.Row and plain tuple connections."""
    return row[name] if isinstance(row, sqlite3.Row) else row[idx]


@dataclass(frozen=True)
class Calibration:
    polled_at: str
    credits_remaining_usd: float | None
    multiplier: float
    multiplier_clamped: bool
    poll_status: str  # 'ok' | 'stale' | 'http_error' | 'timeout' | 'missing_key'


def current_calibration(conn: sqlite3.Connection) -> Calibration | None:
    """Return latest cost_calibration row, or None if table is empty.

    If ``polled_at`` is older than STALE_AFTER, ``poll_status`` is rewritten
    to ``'stale'`` regardless of the stored value — the freshness check is
    derived, not persisted.
    """
    row = conn.execute(
        """SELECT polled_at, credits_remaining_usd, multiplier,
                  multiplier_clamped, poll_status
           FROM cost_calibration
           ORDER BY id DESC
           LIMIT 1"""
    ).fetchone()
    if row is None:
        return None

    polled_at_str = _get(row, 0, "polled_at")
    polled_at_dt = datetime.strptime(polled_at_str, "%Y-%m-%d %H:%M:%S")
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    poll_status = "stale" if (now_utc - polled_at_dt) > STALE_AFTER else _get(row, 4, "poll_status")

    return Calibration(
        polled_at=polled_at_str,
        credits_remaining_usd=_get(row, 1, "credits_remaining_usd"),
        multiplier=_get(row, 2, "multiplier") or 1.0,
        multiplier_clamped=bool(_get(row, 3, "multiplier_clamped")),
        poll_status=poll_status,
    )


@dataclass(frozen=True)
class OpRow:
    operation: str
    cost_usd: float
    n_calls: int


def _multiplier(conn: sqlite3.Connection) -> float:
    cal = current_calibration(conn)
    return cal.multiplier if cal else 1.0


def per_job_cost(conn: sqlite3.Connection, job_id: str) -> float | None:
    """Calibrated sum of cost_log.cost_usd for one job.

    Returns None if every cost_log row for the job has NULL cost_usd
    (or no rows exist). The "—" rendering in templates is the caller's
    responsibility.
    """
    row = conn.execute(
        """SELECT SUM(cost_usd) AS total
           FROM cost_log
           WHERE job_id = ? AND cost_usd IS NOT NULL""",
        (job_id,),
    ).fetchone()
    total = _get(row, 0, "total")
    if total is None:
        return None
    return float(total) * _multiplier(conn)


def per_job_breakdown(conn: sqlite3.Connection, job_id: str) -> list[OpRow]:
    """Per-operation calibrated cost breakdown for one job."""
    multiplier = _multiplier(conn)
    rows = conn.execute(
        """SELECT operation, SUM(cost_usd) AS total, COUNT(*) AS n
           FROM cost_log
           WHERE job_id = ? AND cost_usd IS NOT NULL
           GROUP BY operation
           ORDER BY total DESC""",
        (job_id,),
    ).fetchall()
    return [
        OpRow(
            operation=_get(r, 0, "operation"),
            cost_usd=float(_get(r, 1, "total")) * multiplier,
            n_calls=int(_get(r, 2, "n")),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class WeekRow:
    week_start: str  # YYYY-MM-DD, UTC Sunday-anchored
    total_usd: float


def weekly_spend(conn: sqlite3.Connection, weeks: int = 4) -> list[WeekRow]:
    """Calibrated prep spend per week, oldest-first.

    Always returns exactly ``weeks`` rows, oldest first, with zero-filled
    entries for weeks that have no spend. Anchored at UTC Sundays — both
    producer (cost_tracking.log_call writes datetime('now')) and consumer
    use UTC. The dashboard widget should label the X-axis as UTC weeks
    rather than PT weeks if precision matters. Excludes 'score' operation
    (out of scope per AC 2; surface is prep-spend specific). Excludes NULL
    cost_usd rows.

    Week anchors use ``date(d, '-' || strftime('%w', d) || ' days')`` to
    compute the Sunday that starts each week. This is correct for all
    days including Sunday itself (the ``'weekday 0', '-7 days'`` idiom
    is broken on Sundays — it returns the previous Sunday instead of the
    day itself).
    """
    if weeks < 1:
        raise ValueError("weeks must be >= 1")
    multiplier = _multiplier(conn)
    oldest_weeks = weeks - 1
    rows = conn.execute(
        """WITH RECURSIVE week_series(week_start, n) AS (
               -- Start at the oldest Sunday anchor and walk forward
               SELECT date('now', '-' || strftime('%w', 'now') || ' days',
                           ?),
                      0
               UNION ALL
               SELECT date(week_start, '+7 days'), n + 1
               FROM week_series
               WHERE n + 1 < ?
           ),
           spend AS (
               SELECT date(logged_at,
                           '-' || strftime('%w', logged_at) || ' days'
                     ) AS week_start,
                      SUM(cost_usd) AS total
               FROM cost_log
               WHERE cost_usd IS NOT NULL
                 AND logged_at IS NOT NULL
                 AND operation != 'score'
                 AND logged_at >= date('now', ?)
               GROUP BY 1
           )
           SELECT ws.week_start,
                  COALESCE(s.total, 0.0) AS total
           FROM week_series ws
           LEFT JOIN spend s ON s.week_start = ws.week_start
           ORDER BY ws.week_start ASC""",
        (f"-{oldest_weeks * 7} days", weeks, f"-{(weeks + 1) * 7} days"),
    ).fetchall()
    return [
        WeekRow(
            week_start=_get(r, 0, "week_start"),
            total_usd=float(_get(r, 1, "total")) * multiplier,
        )
        for r in rows
    ]


def runway_weeks(conn: sqlite3.Connection) -> float | None:
    """Credits remaining ÷ 4-week-rolling avg weekly spend.

    Returns None if there's no spend history (fresh stack) or no
    calibration row (no credits_remaining known).
    """
    cal = current_calibration(conn)
    if cal is None or cal.credits_remaining_usd is None:
        return None
    weeks = weekly_spend(conn, weeks=4)
    avg = sum(w.total_usd for w in weeks) / len(weeks)
    if avg <= 0:
        return None
    return cal.credits_remaining_usd / avg


def projected_monthly(conn: sqlite3.Connection) -> float | None:
    """7-day calibrated spend extrapolated to 30 days.

    Returns None if there's no spend in the last 7 days.
    """
    multiplier = _multiplier(conn)
    row = conn.execute(
        """SELECT SUM(cost_usd) AS total
           FROM cost_log
           WHERE cost_usd IS NOT NULL
             AND logged_at IS NOT NULL
             AND operation != 'score'
             AND logged_at >= datetime('now', '-7 days')"""
    ).fetchone()
    total = _get(row, 0, "total")
    if total is None:
        return None
    return float(total) * multiplier * 30.0 / 7.0
