"""Stats tabs: /stats/, /stats/funnel (14e; #63).

Infrastructure for the `/stats/*` web UI group. Deferred dashboards render as
disabled tabs in stats/_tabs.html until their respective follow-ups ship
(#193 feedback, #194 scoring, #195 rejections, #196 throughput, #197 effectiveness).

Data source: SQLite at request time. No materialized stats tables; pipeline.db
is small enough that a 30-day audit_log scan is sub-10ms.

Canonical funnel stages (top → bottom). Terminal exits rendered as separate
series in the chart, not as continuations of the main funnel.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.web.routes.materials import get_db

router = APIRouter()


# Funnel stages — ordered top-to-bottom through the pipeline. Terminal exits
# (rejected, not_selected, waitlisted) are separate series in the chart.
FUNNEL_STAGES: tuple[str, ...] = (
    "scored",
    "manual_review",
    "prep_in_progress",
    "materials_drafted",
    "applied",
    "interview",
    "offer",
)
TERMINAL_STAGES: tuple[str, ...] = ("rejected", "not_selected", "waitlisted")
ALL_STAGES: tuple[str, ...] = FUNNEL_STAGES + TERMINAL_STAGES

_FUNNEL_WINDOW_DAYS = 30


@router.get("/stats/", response_class=HTMLResponse)
def stats_index() -> RedirectResponse:
    return RedirectResponse(url="/stats/funnel", status_code=307)


@router.get("/stats/funnel", response_class=HTMLResponse)
def funnel(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Daily stage-transition counts over the last _FUNNEL_WINDOW_DAYS."""
    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=_FUNNEL_WINDOW_DAYS - 1)
    # date(changed_at) relies on the canonical naïve-UTC format
    # "YYYY-MM-DD HH:MM:SS" (see CLAUDE.md §audit_log timestamp format).
    placeholders = ",".join("?" * len(ALL_STAGES))
    rows = db.execute(
        f"""
        SELECT date(changed_at) AS day, new_value AS stage, COUNT(*) AS n
        FROM audit_log
        WHERE field_changed = 'stage'
          AND date(changed_at) >= ?
          AND new_value IN ({placeholders})
        GROUP BY day, stage
        ORDER BY day ASC, stage
        """,
        (start_day.isoformat(), *ALL_STAGES),
    ).fetchall()

    daily = _build_daily_matrix(rows, start_day, today)
    totals = {stage: sum(daily[d][stage] for d in daily) for stage in ALL_STAGES}

    # Chart.js payload — server-serialized so no fetch-on-load.
    chart_data = {
        "labels": [d.isoformat() for d in sorted(daily)],
        "datasets": [
            {
                "label": stage,
                "data": [daily[d][stage] for d in sorted(daily)],
                "terminal": stage in TERMINAL_STAGES,
            }
            for stage in ALL_STAGES
        ],
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="stats/funnel.html",
        context={
            "tab": "funnel",
            "window_days": _FUNNEL_WINDOW_DAYS,
            "start_day": start_day.isoformat(),
            "end_day": today.isoformat(),
            "stages": ALL_STAGES,
            "funnel_stages": FUNNEL_STAGES,
            "terminal_stages": TERMINAL_STAGES,
            "daily_table": [
                {"day": d.isoformat(), **{stage: daily[d][stage] for stage in ALL_STAGES}}
                for d in sorted(daily, reverse=True)
            ],
            "totals": totals,
            "chart_data_json": json.dumps(chart_data),
        },
    )


def _build_daily_matrix(
    rows: list[sqlite3.Row],
    start_day: date,
    end_day: date,
) -> dict[date, dict[str, int]]:
    """Expand sparse GROUP BY rows into a dense day × stage matrix.

    Days with no transitions get explicit zero cells so Chart.js draws a
    continuous line rather than interpolating gaps.
    """
    matrix: dict[date, dict[str, int]] = {}
    cursor = start_day
    while cursor <= end_day:
        matrix[cursor] = dict.fromkeys(ALL_STAGES, 0)
        cursor += timedelta(days=1)
    for row in rows:
        day_str = row["day"] if isinstance(row, sqlite3.Row) else row[0]
        stage = row["stage"] if isinstance(row, sqlite3.Row) else row[1]
        n = row["n"] if isinstance(row, sqlite3.Row) else row[2]
        if not day_str:
            continue
        day = date.fromisoformat(day_str)
        if day in matrix and stage in matrix[day]:
            matrix[day][stage] = n
    return matrix
