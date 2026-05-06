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
    heuristic_sum = _heuristic_sum(conn)
    pipeline_actual = max(0.0, credits["total_usage"] - onboarding_total)

    if heuristic_sum <= 0:
        multiplier_raw = 1.0
    else:
        multiplier_raw = pipeline_actual / heuristic_sum

    multiplier = max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, multiplier_raw))
    multiplier_clamped = 1 if multiplier != multiplier_raw else 0

    _insert_row(
        conn,
        poll_status="ok",
        credits_total=credits["total_credits"],
        credits_used=credits["total_usage"],
        onboarding_total=onboarding_total,
        pipeline_actual=pipeline_actual,
        heuristic_sum=heuristic_sum,
        multiplier=multiplier,
        multiplier_clamped=multiplier_clamped,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db_path = BASE / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    try:
        poll_once(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
