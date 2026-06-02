"""Monthly LLM spend-ceiling enforcement helpers (#671).

Two surfaces:

- :func:`check_call_gate` — called inside ``openrouter.complete()`` before
  every HTTP request. Opens a short-lived DB connection, checks the current
  calendar-month spend, and raises :class:`LLMSpendCeilingExceeded` if the
  ceiling is met or exceeded. No-op when the ceiling is disabled or the DB
  is unavailable (so unit tests that don't build a pipeline.db are unaffected).

- :func:`check_launch_gate` — called by the 5 LLM-spawning route handlers
  before they launch a subprocess. Returns a :class:`LaunchGateRefusal`
  dataclass if the ceiling is exceeded, or ``None`` if OK. Takes a caller-
  supplied ``sqlite3.Connection`` so it participates in the route's existing
  DB session.

Both gates fire threshold alerts (#876) at 80% and 100% of the ceiling via
:func:`_maybe_fire_threshold_alerts`. Alerts fire at most once per threshold
per calendar month (deduped via in-process set + ``notifications`` table).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from findajob.config_loader import load_spend_ceiling
from findajob.cost_rollups import spend_this_month
from findajob.db import connect
from findajob.llm.openrouter import LLMSpendCeilingExceeded
from findajob.paths import BASE
from findajob.timeutil import local_tz

log = logging.getLogger(__name__)


_DB_PATH = Path(BASE) / "data" / "pipeline.db"

_alerts_fired: set[str] = set()

_WARNING_THRESHOLD = 0.80


@dataclass(frozen=True)
class LaunchGateRefusal:
    """Returned by :func:`check_launch_gate` when the ceiling is exceeded."""

    ceiling_usd: float
    current_sum_usd: float


def _month_key(tz: str) -> str:
    """Return ``YYYY-MM`` in the stack's local timezone."""
    return datetime.now(ZoneInfo(tz)).strftime("%Y-%m")


def _already_sent_this_month(kind: str, month: str, conn: sqlite3.Connection) -> bool:
    """Check whether a notification of *kind* was already persisted this month.

    ``notifications.sent_at`` is UTC (``datetime('now')``). We need the
    TZ-aware month boundaries to match ``spend_this_month()``'s reset.
    """
    tz = local_tz()
    zi = ZoneInfo(tz)
    year, mon = int(month[:4]), int(month[5:7])
    start_local = datetime(year, mon, 1, tzinfo=zi)
    if mon == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=zi)
    else:
        end_local = datetime(year, mon + 1, 1, tzinfo=zi)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = end_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT 1 FROM notifications WHERE kind = ? AND sent_at >= ? AND sent_at < ? LIMIT 1",
        (kind, start_utc, end_utc),
    ).fetchone()
    return row is not None


def _maybe_fire_threshold_alerts(current: float, ceiling: float, conn: sqlite3.Connection) -> None:
    """Fire ntfy alerts at 80% and 100% of the monthly ceiling.

    Observability, not safety — wrapped in try/except so a notification
    failure never prevents the gate from enforcing.
    """
    try:
        month = _month_key(local_tz())
        thresholds: list[tuple[str, float, str, str, str]] = []

        if current >= ceiling:
            thresholds.append(
                (
                    "spend_ceiling_reached",
                    ceiling,
                    f"Spend ceiling reached: ${current:.2f} / ${ceiling:.2f}",
                    f"Monthly LLM spend has hit the ceiling (${current:.2f} / ${ceiling:.2f}). "
                    "LLM calls are blocked until the ceiling is raised or the month resets.",
                    "urgent",
                )
            )

        if current >= ceiling * _WARNING_THRESHOLD:
            thresholds.append(
                (
                    "spend_ceiling_warning",
                    ceiling * _WARNING_THRESHOLD,
                    f"Approaching spend ceiling: ${current:.2f} / ${ceiling:.2f}",
                    f"Monthly LLM spend is at {current / ceiling:.0%} of the ceiling "
                    f"(${current:.2f} / ${ceiling:.2f}).",
                    "high",
                )
            )

        for kind, _thresh, title, body, priority in thresholds:
            cache_key = f"{kind}:{month}"
            if cache_key in _alerts_fired:
                continue
            if _already_sent_this_month(kind, month, conn):
                _alerts_fired.add(cache_key)
                continue
            from findajob.notifications.ntfy import send

            send(
                title=title,
                body=body,
                priority=priority,
                tags="warning" if kind == "spend_ceiling_warning" else "rotating_light",
                kind=kind,
                cta_url=None,
            )
            _alerts_fired.add(cache_key)
    except Exception:
        log.exception("spend-ceiling alert failed (non-fatal)")


def check_call_gate() -> None:
    """Raise :class:`LLMSpendCeilingExceeded` if the monthly ceiling is exceeded.

    Called inside ``openrouter.complete()`` before any HTTP work. Opens its
    own DB connection and closes it immediately so it doesn't interfere with
    callers that manage their own connections. No-op when the ceiling is
    not configured (``load_spend_ceiling()`` returns ``None``). DB errors
    propagate — the gate is a safety mechanism, not best-effort.
    """
    ceiling = load_spend_ceiling()
    if ceiling is None:
        return

    conn = connect(_DB_PATH)
    try:
        current = spend_this_month(conn, tz=local_tz())
        _maybe_fire_threshold_alerts(current, ceiling, conn)
    finally:
        conn.close()

    if current >= ceiling:
        raise LLMSpendCeilingExceeded(ceiling_usd=ceiling, current_sum_usd=current)


def check_launch_gate(conn: sqlite3.Connection) -> LaunchGateRefusal | None:
    """Return a :class:`LaunchGateRefusal` if the ceiling is exceeded, else ``None``.

    Takes a caller-supplied connection so the route handler's existing DB
    session is reused — no extra connection overhead.
    """
    ceiling = load_spend_ceiling()
    if ceiling is None:
        return None

    current = spend_this_month(conn, tz=local_tz())
    _maybe_fire_threshold_alerts(current, ceiling, conn)
    if current >= ceiling:
        return LaunchGateRefusal(ceiling_usd=ceiling, current_sum_usd=current)

    return None
