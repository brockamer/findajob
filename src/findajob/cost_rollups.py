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
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


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
    week_start: str  # YYYY-MM-DD, local-tz Sunday-anchored
    prep_usd: float
    scoring_usd: float


def _week_anchors_utc(tz: str, weeks: int) -> list[tuple[str, str, str]]:
    """Return [(week_start_local_iso, start_utc, end_utc), ...] oldest-first.

    Boundaries are Sunday 00:00 in the given IANA tz, converted to UTC for
    filtering ``cost_log.logged_at`` (which is stored as UTC).
    ``astimezone(UTC)`` is DST-correct: each anchor's offset is resolved
    against the local datetime it represents, so a week straddling a DST
    transition gets the correct UTC bounds for that week's local
    interpretation.
    """
    tz_info = ZoneInfo(tz)
    now_local = datetime.now(tz_info)
    # Python weekday(): Mon=0..Sun=6. We want Sunday-anchored weeks,
    # so days since the most recent Sunday = (weekday + 1) % 7.
    days_since_sunday = (now_local.weekday() + 1) % 7
    this_sunday_local = (now_local - timedelta(days=days_since_sunday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    anchors: list[tuple[str, str, str]] = []
    for i in range(weeks):
        offset_weeks_back = weeks - 1 - i  # oldest first
        start_local = this_sunday_local - timedelta(weeks=offset_weeks_back)
        end_local = start_local + timedelta(weeks=1)
        start_utc = start_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        end_utc = end_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        anchors.append((start_local.date().isoformat(), start_utc, end_utc))
    return anchors


def weekly_spend(conn: sqlite3.Connection, weeks: int = 4, tz: str = "UTC") -> list[WeekRow]:
    """Weekly spend per week (prep + scoring breakdown), oldest-first.

    Always returns exactly ``weeks`` rows, oldest first, with zero-filled
    entries for weeks that have no spend. Anchored at Sunday 00:00 in the
    given IANA timezone (default UTC; pass e.g. ``"America/Los_Angeles"``
    to match operator local time). The producer (``cost_tracking.log_call``
    writes ``datetime('now')``) stores UTC; boundary conversion happens in
    Python before the SQL filter, so DST transitions resolve correctly.

    Splits the per-week total into ``prep_usd`` (operation != 'score') and
    ``scoring_usd`` (operation = 'score') so the dashboard widget can
    surface both — a widget that only counts prep reads $0.00 during
    low-prep stretches even when scoring spent tens of dollars. Excludes
    NULL ``cost_usd`` rows.
    """
    if weeks < 1:
        raise ValueError("weeks must be >= 1")
    anchors = _week_anchors_utc(tz, weeks)
    placeholders = ", ".join(["(?, ?, ?, ?)"] * weeks)
    params: list[object] = []
    for idx, (week_start, start_utc, end_utc) in enumerate(anchors):
        params.extend([idx, week_start, start_utc, end_utc])
    rows = conn.execute(
        f"""WITH weeks(idx, week_start, start_utc, end_utc) AS (
               VALUES {placeholders}
           )
           SELECT w.week_start,
                  COALESCE(SUM(CASE WHEN c.operation != 'score'
                                    THEN c.cost_usd END), 0.0) AS prep,
                  COALESCE(SUM(CASE WHEN c.operation = 'score'
                                    THEN c.cost_usd END), 0.0) AS scoring
           FROM weeks w
           LEFT JOIN cost_log c
             ON c.logged_at >= w.start_utc
            AND c.logged_at <  w.end_utc
            AND c.cost_usd IS NOT NULL
            AND c.logged_at IS NOT NULL
           GROUP BY w.idx, w.week_start
           ORDER BY w.idx ASC""",
        params,
    ).fetchall()
    return [
        WeekRow(
            week_start=_get(r, 0, "week_start"),
            prep_usd=float(_get(r, 1, "prep")),
            scoring_usd=float(_get(r, 2, "scoring")),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class ProjectedMonthly:
    prep_usd: float | None
    scoring_usd: float | None


def projected_monthly(conn: sqlite3.Connection) -> ProjectedMonthly:
    """7-day spend extrapolated to 30 days, split prep vs scoring.

    Each field is ``None`` when no rows of that flavor exist in the last
    7 days, ``float`` otherwise. Split exists for the same reason as
    ``weekly_spend``: a prep-only projection reads $0.00 during low-prep
    stretches even when scoring is actively spending.
    """
    row = conn.execute(
        """SELECT
              SUM(CASE WHEN operation != 'score' THEN cost_usd END) AS prep,
              SUM(CASE WHEN operation = 'score' THEN cost_usd END) AS scoring
           FROM cost_log
           WHERE cost_usd IS NOT NULL
             AND logged_at IS NOT NULL
             AND logged_at >= datetime('now', '-7 days')"""
    ).fetchone()
    prep = _get(row, 0, "prep")
    scoring = _get(row, 1, "scoring")
    return ProjectedMonthly(
        prep_usd=float(prep) * 30.0 / 7.0 if prep is not None else None,
        scoring_usd=float(scoring) * 30.0 / 7.0 if scoring is not None else None,
    )


def _month_anchors_utc(now_local: datetime) -> tuple[str, str]:
    """Return ``(this_month_start_utc, next_month_start_utc)`` as
    ``"%Y-%m-%d %H:%M:%S"`` strings matching ``cost_log.logged_at`` storage.

    Boundaries are local-month-start (taken from ``now_local.tzinfo``),
    converted to UTC via ``astimezone(UTC)`` — DST-correct since each anchor's
    offset is resolved against the local datetime it represents. Mirrors
    :func:`_week_anchors_utc`'s pattern for monthly cadence.

    ``now_local`` must be a tz-aware datetime. Production callers pass
    ``datetime.now(ZoneInfo(tz))``; tests pass frozen tz-aware datetimes.
    """
    if now_local.tzinfo is None:
        raise ValueError("now_local must be tz-aware")
    this_month_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if this_month_local.month == 12:
        next_month_local = this_month_local.replace(year=this_month_local.year + 1, month=1)
    else:
        next_month_local = this_month_local.replace(month=this_month_local.month + 1)
    return (
        this_month_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        next_month_local.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


def spend_this_month(
    conn: sqlite3.Connection,
    tz: str = "UTC",
    *,
    now: datetime | None = None,
) -> float:
    """Current calendar-month spend in USD. Returns 0.0 if no rows.

    Used by the nav chip and the spend-ceiling gates. The month boundary is
    resolved in the given IANA timezone — operators in PT, JST, etc. see
    the counter reset at their local month boundary rather than UTC's
    (the difference can be up to 14h depending on offset). Default ``"UTC"``
    preserves pre-#823 behavior for callers that don't pass a tz.

    Callers read ``os.environ.get("TZ") or "UTC"`` and pass it through,
    matching :func:`weekly_spend` and the codebase precedent at
    ``findajob/web/routes/landing.py:42``.

    Args:
        conn: SQLite connection.
        tz: IANA timezone name (default ``"UTC"``).
        now: Test seam — a tz-aware datetime used in place of
            ``datetime.now(ZoneInfo(tz))``. If passed, its tzinfo wins
            (tests should pass datetimes whose tz matches the ``tz`` arg).
    """
    now_local = now if now is not None else datetime.now(ZoneInfo(tz))
    start_utc, end_utc = _month_anchors_utc(now_local)
    row = conn.execute(
        """SELECT SUM(cost_usd) AS total
           FROM cost_log
           WHERE cost_usd IS NOT NULL
             AND logged_at IS NOT NULL
             AND logged_at >= ?
             AND logged_at <  ?""",
        (start_utc, end_utc),
    ).fetchone()
    total = _get(row, 0, "total")
    return float(total) if total is not None else 0.0
