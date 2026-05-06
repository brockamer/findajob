#!/usr/bin/env python3
"""#87 — Poll OpenRouter for credits balance and write cost_calibration row.

Runs every 5 min via supercronic. Joins the live credits balance with the
stack's `cost_log` heuristic sum and `onboarding_sessions` total to produce
a calibration multiplier (workaround for #463 onboarding/cost_log unification).

No-ops on missing OPENROUTER_API_KEY beyond writing one row marking the state
— surfaces use that to render "(no key)" instead of stale OK numbers.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Make `from findajob.paths import BASE` work when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from findajob.paths import BASE  # noqa: E402

LOG = logging.getLogger("poll_openrouter_credits")

OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
HTTP_TIMEOUT_S = 30
MULTIPLIER_MIN = 0.5
MULTIPLIER_MAX = 3.0
WINDOW_DAYS = 7


class OpenRouterHTTPError(Exception):
    """Raised on any non-2xx response from OpenRouter."""


def _fetch_credits(api_key: str) -> dict[str, float]:
    """GET /api/v1/credits. Returns {'total_credits': X, 'total_usage': Y}.

    Raises OpenRouterHTTPError on HTTP errors (so callers can record
    poll_status='http_error' with the upstream message).
    """
    req = urllib.request.Request(  # noqa: S310 — fixed https URL
        OPENROUTER_CREDITS_URL,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "HTTP-Referer": "https://github.com/brockamer/findajob",
            "X-Title": "findajob cost calibration poll",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OpenRouterHTTPError(f"{e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise OpenRouterHTTPError(f"network error: {e.reason}") from e

    data = body.get("data", body)  # OpenRouter wraps in {"data": {...}}
    return {
        "total_credits": float(data.get("total_credits", 0.0)),
        "total_usage": float(data.get("total_usage", 0.0)),
    }


def _heuristic_sum(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log WHERE cost_usd IS NOT NULL").fetchone()
    return float(row[0])


def _window_baseline(conn: sqlite3.Connection, days: int) -> dict[str, float] | None:
    """Most recent 'ok' cost_calibration row at least `days` days old.

    Returns {'credits_used_usd': X, 'onboarding_total_usd': Y} or None
    when no eligible row exists (warming-up state).
    """
    row = conn.execute(
        """SELECT credits_used_usd, onboarding_total_usd
           FROM cost_calibration
           WHERE poll_status = 'ok'
             AND polled_at <= datetime('now', '-' || ? || ' days')
           ORDER BY id DESC LIMIT 1""",
        (days,),
    ).fetchone()
    if row is None:
        return None
    return {
        "credits_used_usd": float(row[0] or 0.0),
        "onboarding_total_usd": float(row[1] or 0.0),
    }


def _heuristic_sum_windowed(conn: sqlite3.Connection, days: int) -> float:
    """SUM(cost_log.cost_usd) for cost_usd IS NOT NULL rows logged within the window."""
    row = conn.execute(
        """SELECT COALESCE(SUM(cost_usd), 0)
           FROM cost_log
           WHERE cost_usd IS NOT NULL
             AND logged_at >= datetime('now', '-' || ? || ' days')""",
        (days,),
    ).fetchone()
    return float(row[0])


def _last_good_multiplier(conn: sqlite3.Connection) -> float | None:
    """Most recent cost_calibration.multiplier where poll_status='ok'.

    Used to inherit the multiplier on sparse-week polls
    (no new heuristic activity in the window).
    """
    row = conn.execute(
        """SELECT multiplier FROM cost_calibration
           WHERE poll_status = 'ok' AND multiplier IS NOT NULL
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def _onboarding_total(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cumulative_cost_usd), 0) FROM onboarding_sessions").fetchone()
    return float(row[0])


def _insert_row(
    conn: sqlite3.Connection,
    *,
    poll_status: str,
    credits_total: float | None = None,
    credits_used: float | None = None,
    onboarding_total: float | None = None,
    heuristic_sum: float | None = None,
    pipeline_actual: float | None = None,
    multiplier: float | None = None,
    multiplier_clamped: int = 0,
    error_message: str | None = None,
) -> None:
    credits_remaining = credits_total - credits_used if credits_total is not None and credits_used is not None else None
    conn.execute(
        """INSERT INTO cost_calibration
           (credits_total_usd, credits_used_usd, credits_remaining_usd,
            onboarding_total_usd, pipeline_actual_usd, heuristic_sum_usd,
            multiplier, multiplier_clamped, poll_status, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            credits_total,
            credits_used,
            credits_remaining,
            onboarding_total,
            pipeline_actual,
            heuristic_sum,
            multiplier,
            multiplier_clamped,
            poll_status,
            error_message,
        ),
    )
    conn.commit()


def poll_once(conn: sqlite3.Connection) -> None:
    """One poll cycle. All branches end with exactly one INSERT."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        _insert_row(conn, poll_status="missing_key")
        LOG.warning("OPENROUTER_API_KEY unset; recorded missing_key row")
        return

    try:
        credits = _fetch_credits(api_key)
    except OpenRouterHTTPError as e:
        _insert_row(conn, poll_status="http_error", error_message=str(e))
        LOG.error("OpenRouter credits poll failed: %s", e)
        return

    onboarding_total = _onboarding_total(conn)
    heuristic_sum = _heuristic_sum(conn)  # lifetime, stored for observability
    pipeline_actual_lifetime = max(0.0, credits["total_usage"] - onboarding_total)

    # Windowed delta math (#467) — replaces lifetime-cumulative comparison.
    baseline = _window_baseline(conn, WINDOW_DAYS)

    if baseline is None:
        # No `WINDOW_DAYS`-old 'ok' row exists yet — warming up.
        # Store this row as future baseline; surfaces render uncalibrated.
        _insert_row(
            conn,
            poll_status="warming_up",
            credits_total=credits["total_credits"],
            credits_used=credits["total_usage"],
            onboarding_total=onboarding_total,
            pipeline_actual=pipeline_actual_lifetime,
            heuristic_sum=heuristic_sum,
            multiplier=1.0,
            multiplier_clamped=0,
        )
        return

    delta_credits = max(0.0, credits["total_usage"] - baseline["credits_used_usd"])
    delta_onboarding = max(0.0, onboarding_total - baseline["onboarding_total_usd"])
    pipeline_actual_window = max(0.0, delta_credits - delta_onboarding)
    heuristic_window = _heuristic_sum_windowed(conn, WINDOW_DAYS)

    if heuristic_window <= 0:
        # Sparse week — no prep activity in the window. Inherit last good
        # multiplier rather than emitting a clamped/garbage value.
        last_mult = _last_good_multiplier(conn)
        multiplier = last_mult if last_mult is not None else 1.0
        multiplier_clamped = 0
    else:
        multiplier_raw = pipeline_actual_window / heuristic_window
        multiplier = max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, multiplier_raw))
        multiplier_clamped = 1 if multiplier != multiplier_raw else 0

    _insert_row(
        conn,
        poll_status="ok",
        credits_total=credits["total_credits"],
        credits_used=credits["total_usage"],
        onboarding_total=onboarding_total,
        pipeline_actual=pipeline_actual_lifetime,  # lifetime, for next poll's baseline
        heuristic_sum=heuristic_sum,  # lifetime, observability
        multiplier=multiplier,
        multiplier_clamped=multiplier_clamped,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db_path = Path(BASE) / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    try:
        poll_once(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
