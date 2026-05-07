"""SQL helpers for cost-visibility surfaces.

Pure functions over a sqlite3.Connection. No HTTP, no env reads, no
side effects beyond the SELECTs they execute. UI routes and notify.py
both consume this module so the cost math lives in one place.

Cost numbers come from ``cost_log.cost_usd``, written natively by
``findajob.llm.openrouter`` from ``response.usage.cost``. No calibration,
no multiplier — the OpenRouter API is the authoritative source.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


def _get(row: sqlite3.Row | tuple, idx: int, name: str):
    """Defensive accessor for both sqlite3.Row and plain tuple connections."""
    return row[name] if isinstance(row, sqlite3.Row) else row[idx]


@dataclass(frozen=True)
class OpRow:
    operation: str
    cost_usd: float
    n_calls: int


def per_job_cost(conn: sqlite3.Connection, job_id: str) -> float | None:
    """Sum of cost_log.cost_usd for one job.

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
    return float(total)


def per_job_breakdown(conn: sqlite3.Connection, job_id: str) -> list[OpRow]:
    """Per-operation cost breakdown for one job."""
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
            cost_usd=float(_get(r, 1, "total")),
            n_calls=int(_get(r, 2, "n")),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class WeekRow:
    week_start: str  # YYYY-MM-DD, UTC Sunday-anchored
    total_usd: float


def weekly_spend(conn: sqlite3.Connection, weeks: int = 4) -> list[WeekRow]:
    """Prep spend per week, oldest-first.

    Always returns exactly ``weeks`` rows, oldest first, with zero-filled
    entries for weeks that have no spend. Anchored at UTC Sundays — both
    producer (cost_tracking.log_call writes datetime('now')) and consumer
    use UTC. The dashboard widget should label the X-axis as UTC weeks
    rather than PT weeks if precision matters. Excludes 'score' operation
    (out of scope; surface is prep-spend specific). Excludes NULL
    cost_usd rows.

    Week anchors use ``date(d, '-' || strftime('%w', d) || ' days')`` to
    compute the Sunday that starts each week. This is correct for all
    days including Sunday itself (the ``'weekday 0', '-7 days'`` idiom
    is broken on Sundays — it returns the previous Sunday instead of the
    day itself).
    """
    if weeks < 1:
        raise ValueError("weeks must be >= 1")
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
            total_usd=float(_get(r, 1, "total")),
        )
        for r in rows
    ]


def projected_monthly(conn: sqlite3.Connection) -> float | None:
    """7-day spend extrapolated to 30 days.

    Returns None if there's no spend in the last 7 days.
    """
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
    return float(total) * 30.0 / 7.0


def spend_this_month(conn: sqlite3.Connection) -> float:
    """Current calendar-month spend in USD. Returns 0.0 if no rows.

    Used by the nav chip. UTC month boundary, matches the rest of the
    cost rollups.
    """
    row = conn.execute(
        """SELECT SUM(cost_usd) AS total
           FROM cost_log
           WHERE cost_usd IS NOT NULL
             AND logged_at IS NOT NULL
             AND strftime('%Y-%m', logged_at) = strftime('%Y-%m', 'now')"""
    ).fetchone()
    total = _get(row, 0, "total")
    return float(total) if total is not None else 0.0
