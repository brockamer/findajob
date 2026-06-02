"""Bucket naïve-UTC DB timestamps onto the operator's configured local calendar.

The pipeline stores ``audit_log`` / ``feedback_log`` timestamps as naïve UTC
strings ("YYYY-MM-DD HH:MM:SS"; see CLAUDE.md §audit_log timestamp format). The
operator's calendar runs in whatever timezone the deployment configures via the
``TZ`` environment variable (any IANA zone — ``America/Los_Angeles``,
``Europe/Berlin``, ``Asia/Tokyo``, …; falls back to ``UTC`` when unset), so
"today" and daily windows must bucket on the *local* day. A transition made late
in the local evening is stored as the next UTC day and would land in the wrong
daily bucket under a naïve ``date()`` — these helpers fix that.

This centralizes the stack-timezone read: ``local_tz()`` reads ``TZ`` once and
everything else in this module flows through it. New code that needs the local
zone (for display or calendar math) should obtain it here rather than re-reading
``TZ`` or hardcoding a zone. It reuses the ``astimezone(UTC)`` idiom proven
DST-correct in ``cost_rollups``; every helper takes ``tz``/``now`` seams so
callers (and tests) can be deterministic without touching process-global ``TZ``.
(``cost_rollups`` and ``spend_ceiling`` predate this module and still read ``TZ``
directly — folding them onto ``local_tz`` is a follow-up refactor, not a behavior
change.)
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

# Canonical naïve-UTC format used by write_audit() / datetime('now').
_DB_TS_FMT = "%Y-%m-%d %H:%M:%S"


def local_tz() -> str:
    """IANA tz name the operator's calendar runs on (deployment ``TZ``; UTC fallback)."""
    return os.environ.get("TZ") or "UTC"


def local_zoneinfo(tz: str | None = None) -> ZoneInfo:
    """``ZoneInfo`` for the stack's configured timezone — the single place display
    and bucketing code should obtain the local zone instead of hardcoding one."""
    return ZoneInfo(tz or local_tz())


def today_local(tz: str | None = None, now: datetime | None = None) -> date:
    """Current calendar date in ``tz``. ``now`` is a test seam (tz-aware datetime)."""
    zi = local_zoneinfo(tz)
    moment = now.astimezone(zi) if now is not None else datetime.now(zi)
    return moment.date()


def day_window_start_utc(days: int, tz: str | None = None, now: datetime | None = None) -> str:
    """Naïve-UTC string for local-midnight of ``(today_local - (days - 1))``.

    Use as the inclusive lower bound of a ``days``-long window:
    ``WHERE changed_at >= ?``. DST-correct — the UTC offset is resolved against
    the local datetime each boundary represents.
    """
    zi = local_zoneinfo(tz)
    today = today_local(tz, now)
    start_local = datetime(today.year, today.month, today.day, tzinfo=zi) - timedelta(days=days - 1)
    return start_local.astimezone(UTC).strftime(_DB_TS_FMT)


def utc_str_to_local_date(ts: str, tz: str | None = None) -> date:
    """Convert a naïve-UTC DB timestamp string to its ``tz`` calendar date."""
    zi = local_zoneinfo(tz)
    return datetime.strptime(ts, _DB_TS_FMT).replace(tzinfo=UTC).astimezone(zi).date()
