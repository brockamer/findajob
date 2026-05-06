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
