"""Statistical helpers for the tuning-loop stats pages.

Wilson score confidence intervals, min-N gating, and stratification
utilities. All stats pages import from here so the math stays in one place.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date


def wilson_ci(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Returns (lower, upper) as fractions in [0, 1]. When total is 0,
    returns (0.0, 0.0).
    """
    if total <= 0:
        return (0.0, 0.0)
    successes = min(successes, total)

    # z-score lookup for common confidence levels; fall back to 1.96
    z_table = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
    z = z_table.get(confidence, 1.96)

    p_hat = successes / total
    z2 = z * z
    denom = 1 + z2 / total
    centre = p_hat + z2 / (2 * total)
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z2 / (4 * total)) / total)

    lower = max(0.0, (centre - spread) / denom)
    upper = min(1.0, (centre + spread) / denom)
    return (lower, upper)


def wilson_ci_pct(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Wilson CI as percentages, plus the point estimate.

    Returns (pct, lower_pct, upper_pct). Convenience wrapper for template
    rendering where everything is displayed as "42.1% [38.2%, 46.0%]".
    """
    if total <= 0:
        return (0.0, 0.0, 0.0)
    lo, hi = wilson_ci(successes, total, confidence)
    pct = 100.0 * successes / total
    return (round(pct, 1), round(100.0 * lo, 1), round(100.0 * hi, 1))


def min_n_gate(n: int, threshold: int = 20) -> bool:
    """True if sample size is sufficient for display. False → render '—'."""
    return n >= threshold


def stratify(
    rows: list[sqlite3.Row | tuple],
    dims: tuple[str | int, ...],
) -> dict[tuple, list]:
    """Group rows by arbitrary dimension columns.

    `dims` can be column names (str, for Row objects) or integer indices
    (for plain tuples). Returns {(val1, val2, ...): [rows...]}.
    """
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        key = tuple(row[d] for d in dims)  # type: ignore[index]
        groups[key].append(row)
    return dict(groups)


def config_change_markers(
    conn: sqlite3.Connection,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Fetch config_changes rows as chart annotation markers.

    Returns list of {date, lever, summary} dicts suitable for Chart.js
    annotation plugin rendering.
    """
    clauses = []
    params: list[str] = []
    if start_date:
        clauses.append("date(changed_at) >= ?")
        params.append(start_date.isoformat())
    if end_date:
        clauses.append("date(changed_at) <= ?")
        params.append(end_date.isoformat())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT date(changed_at) AS day, lever, change_summary
        FROM config_changes
        {where}
        ORDER BY changed_at ASC
        """,
        params,
    ).fetchall()

    return [
        {
            "date": row["day"] if isinstance(row, sqlite3.Row) else row[0],
            "lever": row["lever"] if isinstance(row, sqlite3.Row) else row[1],
            "summary": ((row["change_summary"] if isinstance(row, sqlite3.Row) else row[2]) or ""),
        }
        for row in rows
    ]


def before_after_metrics(
    conn: sqlite3.Connection,
    change_date: str,
    window_days: int = 7,
) -> dict:
    """Compute key metrics for the window before and after a config change.

    Returns {before: {precision, cost_per_applied, n_scored, n_rejected},
             after:  {precision, cost_per_applied, n_scored, n_rejected},
             delta:  {precision_pct, cost_pct}}.
    """
    result = {}
    for label, audit_filter, cost_filter in [
        (
            "before",
            f"date(changed_at) >= date(?, '-{window_days} days') AND date(changed_at) < date(?)",
            f"date(logged_at) >= date(?, '-{window_days} days') AND date(logged_at) < date(?)",
        ),
        (
            "after",
            f"date(changed_at) >= date(?) AND date(changed_at) < date(?, '+{window_days} days')",
            f"date(logged_at) >= date(?) AND date(logged_at) < date(?, '+{window_days} days')",
        ),
    ]:
        scored_row = conn.execute(
            f"""
            SELECT COUNT(*) FROM audit_log
            WHERE field_changed='stage' AND new_value='scored'
              AND {audit_filter}
            """,
            (change_date, change_date),
        ).fetchone()
        n_scored = scored_row[0] if scored_row else 0

        rejected_row = conn.execute(
            f"""
            SELECT COUNT(*) FROM audit_log
            WHERE field_changed='stage' AND new_value='rejected'
              AND {audit_filter}
            """,
            (change_date, change_date),
        ).fetchone()
        n_rejected = rejected_row[0] if rejected_row else 0

        applied_row = conn.execute(
            f"""
            SELECT COUNT(*) FROM audit_log
            WHERE field_changed='stage' AND new_value='applied'
              AND {audit_filter}
            """,
            (change_date, change_date),
        ).fetchone()
        n_applied = applied_row[0] if applied_row else 0

        cost_row = conn.execute(
            f"""
            SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log
            WHERE {cost_filter}
            """,
            (change_date, change_date),
        ).fetchone()
        total_cost = cost_row[0] if cost_row else 0.0

        precision = (n_rejected / n_scored * 100) if n_scored > 0 else 0.0
        cost_per = (total_cost / n_applied) if n_applied > 0 else 0.0

        result[label] = {
            "precision_pct": round(precision, 1),
            "cost_per_applied": round(cost_per, 2),
            "n_scored": n_scored,
            "n_rejected": n_rejected,
            "n_applied": n_applied,
            "total_cost": round(total_cost, 2),
        }

    before = result["before"]
    after = result["after"]
    precision_delta = round(after["precision_pct"] - before["precision_pct"], 1) if before["n_scored"] > 0 else None
    cost_delta = (
        round(
            (after["cost_per_applied"] - before["cost_per_applied"]) / before["cost_per_applied"] * 100,
            1,
        )
        if before["cost_per_applied"] > 0
        else None
    )

    result["delta"] = {
        "precision_pct": precision_delta,
        "cost_pct": cost_delta,
    }
    return result
