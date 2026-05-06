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

    polled_at_str = row[0] if isinstance(row, tuple) else row["polled_at"]
    polled_at_dt = datetime.strptime(polled_at_str, "%Y-%m-%d %H:%M:%S")
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    poll_status = (
        "stale"
        if (now_utc - polled_at_dt) > STALE_AFTER
        else (row[4] if isinstance(row, tuple) else row["poll_status"])
    )

    return Calibration(
        polled_at=polled_at_str,
        credits_remaining_usd=row[1] if isinstance(row, tuple) else row["credits_remaining_usd"],
        multiplier=(row[2] if isinstance(row, tuple) else row["multiplier"]) or 1.0,
        multiplier_clamped=bool(row[3] if isinstance(row, tuple) else row["multiplier_clamped"]),
        poll_status=poll_status,
    )
