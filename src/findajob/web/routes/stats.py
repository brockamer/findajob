"""Stats tabs: /stats/, /stats/funnel, /stats/feedback, /stats/scoring, /stats/rejections (14e).

Infrastructure for the `/stats/*` web UI group. Deferred dashboards render as
disabled tabs in stats/_tabs.html until their respective follow-ups ship
(#196 throughput, #197 effectiveness).

Data source: SQLite at request time. No materialized stats tables; pipeline.db
is small enough that a 30-day audit_log scan is sub-10ms. The feedback
dashboard deviates from spec AC (jsonl event_type='feedback_stats') and reads
the feedback_log table instead — the spec assumed #55 emitted that event but
it was never wired up, and the table carries the same data with a canonical
timestamp format.

Canonical funnel stages (top → bottom). Terminal exits rendered as separate
series in the chart, not as continuations of the main funnel.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.config_loader import load_reject_reasons
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

# Canonical reject reason options now come from `config/reject_reasons.yaml`
# via `findajob.config_loader.load_reject_reasons()` — single source of truth
# shared with the dropdown in `board/_reject_cell.html`, the filter chips in
# `web/filters/registry.py`, and the prefilter analyzer in
# `findajob.analyze_feedback`. Reasons seen in feedback_log but not in the
# config render after the canonical set (legacy / free-text entries).

_FEEDBACK_WINDOW_DAYS = 28
_FEEDBACK_WEEK_DAYS = 7

_SCORING_WINDOW_DAYS = 30

# /stats/rejections renders an all-time view (no window) because the per-company
# axis is the novel cut — concentration over months is the signal, and the
# 28-day reason-trend slice already lives at /stats/feedback. Top-5 companies
# by absolute count is plenty for v1; rigor (Wilson CI, min-N gates) is the
# v2 work tracked in #230.
_REJECTIONS_TOP_COMPANIES = 5
_REJECTION_STAGES: tuple[str, ...] = ("rejected", "not_selected")

# /stats/throughput renders an all-time per-ISO-week count of stage transitions
# into applied, interview, offer. Stacked-bar series — applied bottom, interview
# middle, offer top — so a single bar reads as the per-week activity stack.
# Source is audit_log, not jobs.stage, because we want event counts: a job that
# moved applied → interview → offer contributes one tick to each series, and
# moved → applied → rejected → reactivated → applied contributes two applied
# ticks. The jobs table only carries the current stage.
_THROUGHPUT_STAGES: tuple[str, ...] = ("applied", "interview", "offer")

# Score columns charted on /stats/scoring. Tuple shape:
#   (column_name, label, range_kind, slug)
# range_kind drives bucketing in _bucketize_scores(): "int_1_10" → 10 integer
# buckets; "float_0_100" → 10 decile buckets ([0,10), [10,20), … [90,100]).
# Coverage differs across columns: relevance_score and interview_likelihood are
# written by the scorer on every job (universal); fit_score and
# probability_score are written by prep Phase B only and are NULL for jobs
# that haven't been promoted. The per-chart subtitle surfaces non-NULL count
# so sparse coverage is a visible signal rather than an empty chart.
SCORING_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("relevance_score", "Relevance score (1-10)", "int_1_10", "relevance"),
    ("interview_likelihood", "Interview likelihood (1-10)", "int_1_10", "interview"),
    ("fit_score", "Fit score (0-100, Phase B only)", "float_0_100", "fit"),
    ("probability_score", "Success probability (0-100, Phase B only)", "float_0_100", "probability"),
)


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


@router.get("/stats/feedback", response_class=HTMLResponse)
def feedback(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Per-reject_reason rejection trends over the last _FEEDBACK_WINDOW_DAYS.

    Two views on one page: a this-week per-reason summary grid (trailing 7 days)
    and a 28-day daily multi-line chart. Source is the feedback_log table; spec
    AC called for a jsonl feedback_stats event that was never implemented.
    """
    today = datetime.now(UTC).date()
    window_start = today - timedelta(days=_FEEDBACK_WINDOW_DAYS - 1)
    week_start = today - timedelta(days=_FEEDBACK_WEEK_DAYS - 1)

    # date(created_at) relies on feedback_log.created_at defaulting to
    # datetime('now'), which writes the canonical naïve-UTC
    # "YYYY-MM-DD HH:MM:SS" format (see init_db.py).
    daily_rows = db.execute(
        """
        SELECT date(created_at) AS day,
               COALESCE(NULLIF(reject_reason, ''), '(blank)') AS reason,
               COUNT(*) AS n
        FROM feedback_log
        WHERE date(created_at) >= ?
        GROUP BY day, reason
        ORDER BY day ASC, reason
        """,
        (window_start.isoformat(),),
    ).fetchall()

    # Merge canonical reasons with anything else found in the window; stable order.
    canonical_reasons, _title_signal = load_reject_reasons()
    extras: list[str] = []
    for row in daily_rows:
        reason = row["reason"] if isinstance(row, sqlite3.Row) else row[1]
        if reason and reason not in canonical_reasons and reason not in extras:
            extras.append(reason)
    reasons: tuple[str, ...] = canonical_reasons + tuple(sorted(extras))

    daily = _build_reason_matrix(daily_rows, reasons, window_start, today)
    window_totals = {r: sum(daily[d][r] for d in daily) for r in reasons}
    week_totals = {r: sum(daily[d][r] for d in daily if d >= week_start) for r in reasons}
    this_week_total = sum(week_totals.values())
    window_total = sum(window_totals.values())

    chart_data = {
        "labels": [d.isoformat() for d in sorted(daily)],
        "datasets": [
            {
                "label": r,
                "data": [daily[d][r] for d in sorted(daily)],
            }
            for r in reasons
        ],
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="stats/feedback.html",
        context={
            "tab": "feedback",
            "window_days": _FEEDBACK_WINDOW_DAYS,
            "week_days": _FEEDBACK_WEEK_DAYS,
            "start_day": window_start.isoformat(),
            "end_day": today.isoformat(),
            "week_start_day": week_start.isoformat(),
            "reasons": reasons,
            "week_totals": week_totals,
            "window_totals": window_totals,
            "this_week_total": this_week_total,
            "window_total": window_total,
            "daily_table": [
                {"day": d.isoformat(), **{r: daily[d][r] for r in reasons}} for d in sorted(daily, reverse=True)
            ],
            "chart_data_json": json.dumps(chart_data),
        },
    )


def _build_reason_matrix(
    rows: list[sqlite3.Row],
    reasons: tuple[str, ...],
    start_day: date,
    end_day: date,
) -> dict[date, dict[str, int]]:
    """Expand sparse (day, reason, n) rows into a dense day × reason matrix."""
    matrix: dict[date, dict[str, int]] = {}
    cursor = start_day
    while cursor <= end_day:
        matrix[cursor] = dict.fromkeys(reasons, 0)
        cursor += timedelta(days=1)
    for row in rows:
        day_str = row["day"] if isinstance(row, sqlite3.Row) else row[0]
        reason = row["reason"] if isinstance(row, sqlite3.Row) else row[1]
        n = row["n"] if isinstance(row, sqlite3.Row) else row[2]
        if not day_str:
            continue
        day = date.fromisoformat(day_str)
        if day in matrix and reason in matrix[day]:
            matrix[day][reason] = n
    return matrix


@router.get("/stats/scoring", response_class=HTMLResponse)
def scoring(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Score-distribution histograms over the last _SCORING_WINDOW_DAYS.

    Sourced from audit_log on stage→scored transitions in the window, joined
    back to `jobs` to read the four score columns. AC #2 named `created_at`
    as the window predicate but that is ingest time, not score time — using
    audit_log scored-transition timestamps matches AC #1's "scored jobs"
    intent.
    """
    today = datetime.now(UTC).date()
    start_day = today - timedelta(days=_SCORING_WINDOW_DAYS - 1)

    scored_id_rows = db.execute(
        """
        SELECT DISTINCT job_id
        FROM audit_log
        WHERE field_changed = 'stage'
          AND new_value = 'scored'
          AND date(changed_at) >= ?
        """,
        (start_day.isoformat(),),
    ).fetchall()
    scored_ids = [r["job_id"] if isinstance(r, sqlite3.Row) else r[0] for r in scored_id_rows]
    total_scored = len(scored_ids)

    histograms: dict[str, list[dict[str, int | str]]] = {}
    coverage: dict[str, dict[str, int]] = {}

    if scored_ids:
        placeholders = ",".join("?" * len(scored_ids))
        for col, _label, kind, slug in SCORING_COLUMNS:
            rows = db.execute(
                f"SELECT {col} AS v FROM jobs WHERE id IN ({placeholders}) AND {col} IS NOT NULL",
                scored_ids,
            ).fetchall()
            values = [r["v"] if isinstance(r, sqlite3.Row) else r[0] for r in rows]
            histograms[slug] = _bucketize_scores(values, kind)
            coverage[slug] = {"with_value": len(values), "total_scored": total_scored}
    else:
        for _col, _label, kind, slug in SCORING_COLUMNS:
            histograms[slug] = _bucketize_scores([], kind)
            coverage[slug] = {"with_value": 0, "total_scored": 0}

    chart_data = {slug: histograms[slug] for slug in (c[3] for c in SCORING_COLUMNS)}

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="stats/scoring.html",
        context={
            "tab": "scoring",
            "window_days": _SCORING_WINDOW_DAYS,
            "start_day": start_day.isoformat(),
            "end_day": today.isoformat(),
            "columns": SCORING_COLUMNS,
            "histograms": histograms,
            "coverage": coverage,
            "total_scored": total_scored,
            "chart_data_json": json.dumps(chart_data),
        },
    )


def _bucketize_scores(values: list[int | float], kind: str) -> list[dict[str, int | str]]:
    """Bucket score values into 10 fixed buckets.

    kind="int_1_10": 10 integer buckets (1, 2, ..., 10).
    kind="float_0_100": 10 decile buckets — [0,10), [10,20), ..., [90,100].
        Value of exactly 100 falls into the last bucket.
    """
    if kind == "int_1_10":
        buckets: list[dict[str, int | str]] = [{"label": str(i), "count": 0} for i in range(1, 11)]
        for v in values:
            iv = int(v)
            if 1 <= iv <= 10:
                buckets[iv - 1]["count"] += 1  # type: ignore[operator]
        return buckets
    if kind == "float_0_100":
        labels = [
            "0-9",
            "10-19",
            "20-29",
            "30-39",
            "40-49",
            "50-59",
            "60-69",
            "70-79",
            "80-89",
            "90-100",
        ]
        buckets = [{"label": label, "count": 0} for label in labels]
        for v in values:
            if v == 100:
                idx = 9
            elif 0 <= v < 100:
                idx = int(v // 10)
            else:
                continue
            buckets[idx]["count"] += 1  # type: ignore[operator]
        return buckets
    return []


@router.get("/stats/rejections", response_class=HTMLResponse)
def rejections(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """All-time rejection breakdown — global per-reason bar + per-company top-5 stacked.

    Sources from `jobs.stage IN ('rejected','not_selected')` so this view
    captures both user rejections (which feedback_log also captures) and
    company NOT_SELECTED events (which never reach feedback_log by design —
    company rejections must not contaminate the scorer's feedback loop).
    Companion to /board/rejected (per-row view) and /stats/feedback (28-day
    user-rejection trend); orthogonal cuts of overlapping data.
    """
    global_rows = db.execute(
        f"""
        SELECT COALESCE(NULLIF(reject_reason, ''), '(blank)') AS reason,
               COUNT(*) AS n
        FROM jobs
        WHERE stage IN ({",".join("?" * len(_REJECTION_STAGES))})
        GROUP BY reason
        ORDER BY n DESC, reason ASC
        """,
        _REJECTION_STAGES,
    ).fetchall()

    canonical_reasons, _title_signal = load_reject_reasons()
    extras: list[str] = []
    for row in global_rows:
        reason = row["reason"] if isinstance(row, sqlite3.Row) else row[0]
        if reason and reason not in canonical_reasons and reason not in extras:
            extras.append(reason)
    reasons: tuple[str, ...] = canonical_reasons + tuple(sorted(extras))

    global_totals: dict[str, int] = dict.fromkeys(reasons, 0)
    for row in global_rows:
        reason = row["reason"] if isinstance(row, sqlite3.Row) else row[0]
        n = row["n"] if isinstance(row, sqlite3.Row) else row[1]
        if reason in global_totals:
            global_totals[reason] = n
    total_rejections = sum(global_totals.values())

    top_company_rows = db.execute(
        f"""
        SELECT company, COUNT(*) AS n
        FROM jobs
        WHERE stage IN ({",".join("?" * len(_REJECTION_STAGES))})
          AND company IS NOT NULL AND TRIM(company) != ''
        GROUP BY company
        ORDER BY n DESC, company ASC
        LIMIT ?
        """,
        (*_REJECTION_STAGES, _REJECTIONS_TOP_COMPANIES),
    ).fetchall()
    top_companies: list[str] = [row["company"] if isinstance(row, sqlite3.Row) else row[0] for row in top_company_rows]
    company_totals: dict[str, int] = {
        (row["company"] if isinstance(row, sqlite3.Row) else row[0]): (
            row["n"] if isinstance(row, sqlite3.Row) else row[1]
        )
        for row in top_company_rows
    }

    per_company: dict[str, dict[str, int]] = {co: dict.fromkeys(reasons, 0) for co in top_companies}
    if top_companies:
        co_placeholders = ",".join("?" * len(top_companies))
        company_rows = db.execute(
            f"""
            SELECT company,
                   COALESCE(NULLIF(reject_reason, ''), '(blank)') AS reason,
                   COUNT(*) AS n
            FROM jobs
            WHERE stage IN ({",".join("?" * len(_REJECTION_STAGES))})
              AND company IN ({co_placeholders})
            GROUP BY company, reason
            """,
            (*_REJECTION_STAGES, *top_companies),
        ).fetchall()
        for row in company_rows:
            co = row["company"] if isinstance(row, sqlite3.Row) else row[0]
            reason = row["reason"] if isinstance(row, sqlite3.Row) else row[1]
            n = row["n"] if isinstance(row, sqlite3.Row) else row[2]
            if co in per_company and reason in per_company[co]:
                per_company[co][reason] = n

    global_chart_data = {
        "labels": list(reasons),
        "data": [global_totals[r] for r in reasons],
    }
    company_chart_data = {
        "labels": top_companies,
        "reasons": list(reasons),
        "datasets": [{"label": r, "data": [per_company[co][r] for co in top_companies]} for r in reasons],
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="stats/rejections.html",
        context={
            "tab": "rejections",
            "reasons": reasons,
            "global_totals": global_totals,
            "total_rejections": total_rejections,
            "top_companies": top_companies,
            "company_totals": company_totals,
            "per_company": per_company,
            "top_n": _REJECTIONS_TOP_COMPANIES,
            "global_chart_data_json": json.dumps(global_chart_data),
            "company_chart_data_json": json.dumps(company_chart_data),
        },
    )


@router.get("/stats/throughput", response_class=HTMLResponse)
def throughput(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Per-ISO-week stage-transition throughput — applied, interview, offer.

    All-time view, no window — operator wants to read multi-month rhythm. ISO
    week label format `YYYY-W##` from SQLite's `strftime('%Y-W%W', ...)`. The
    `%W` token is Monday-based and close-enough to ISO 8601 for at-a-glance
    weekly cadence; strict ISO week-numbering (`%V`) is not used because
    SQLite doesn't expose it and the dashboard reads the same either way.

    Source is audit_log (event counts), not jobs.stage (current state) — see
    the `_THROUGHPUT_STAGES` comment above for the design rationale.
    """
    placeholders = ",".join("?" * len(_THROUGHPUT_STAGES))
    rows = db.execute(
        f"""
        SELECT strftime('%Y-W%W', changed_at) AS week,
               new_value AS stage,
               COUNT(*) AS n
        FROM audit_log
        WHERE field_changed = 'stage'
          AND new_value IN ({placeholders})
          AND changed_at IS NOT NULL
        GROUP BY week, stage
        ORDER BY week ASC, stage
        """,
        _THROUGHPUT_STAGES,
    ).fetchall()

    weekly: dict[str, dict[str, int]] = {}
    for row in rows:
        week = row["week"] if isinstance(row, sqlite3.Row) else row[0]
        stage = row["stage"] if isinstance(row, sqlite3.Row) else row[1]
        n = row["n"] if isinstance(row, sqlite3.Row) else row[2]
        if not week or stage not in _THROUGHPUT_STAGES:
            continue
        weekly.setdefault(week, dict.fromkeys(_THROUGHPUT_STAGES, 0))[stage] = n

    weeks_sorted = sorted(weekly)
    totals = {stage: sum(weekly[w][stage] for w in weekly) for stage in _THROUGHPUT_STAGES}
    grand_total = sum(totals.values())

    chart_data = {
        "labels": weeks_sorted,
        "datasets": [
            {"label": stage, "data": [weekly[w][stage] for w in weeks_sorted]} for stage in _THROUGHPUT_STAGES
        ],
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="stats/throughput.html",
        context={
            "tab": "throughput",
            "stages": _THROUGHPUT_STAGES,
            "weeks": weeks_sorted,
            "weekly": weekly,
            "totals": totals,
            "grand_total": grand_total,
            "chart_data_json": json.dumps(chart_data),
        },
    )
