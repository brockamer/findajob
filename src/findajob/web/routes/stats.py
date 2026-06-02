"""Stats tabs: /stats/funnel, /stats/feedback, /stats/scoring, /stats/rejections,
/stats/throughput, /stats/effectiveness, /stats/recall-audit.

Data source: SQLite at request time. No materialized stats tables; pipeline.db
is small enough that a 30-day audit_log scan is sub-10ms.

Phase 2 rigor (#230): every proportion has a Wilson 95% CI, strata with N<20
render "—", at least one page shows per-source stratification, and config-change
markers appear on trend charts.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from findajob.config_loader import load_reject_reasons
from findajob.metrics.stats import (
    before_after_metrics,
    config_change_markers,
    min_n_gate,
    wilson_ci_pct,
)
from findajob.timeutil import day_window_start_utc, today_local, utc_str_to_local_date
from findajob.web.routes.materials import get_db


def _bucket_by_local_day(rows: list[sqlite3.Row]) -> list[tuple[str, str, int]]:
    """Aggregate raw ``(timestamp, value)`` rows into sparse ``(day_iso, value, n)``
    tuples bucketed on the operator's local calendar day.

    Timestamps are naïve-UTC DB strings; bucketing on the local day (not the UTC
    day) keeps a late-evening local-time transition on the right day (#967). The output
    shape matches what ``_build_daily_matrix`` / ``_build_reason_matrix`` expect
    from a SQL ``GROUP BY`` (they read columns 0/1/2 positionally).
    """
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        ts = row[0]
        value = row[1]
        if not ts:
            continue
        counts[(utc_str_to_local_date(ts).isoformat(), value)] += 1
    return [(day, value, n) for (day, value), n in counts.items()]


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
    today = today_local()
    start_day = today - timedelta(days=_FUNNEL_WINDOW_DAYS - 1)
    # Bucket on the operator's configured-TZ calendar: fetch raw naïve-UTC
    # timestamps and group by local day in Python (#967). The >= bound is local-midnight of
    # start_day expressed in UTC, so the string compare on the canonical
    # "YYYY-MM-DD HH:MM:SS" format is correct.
    placeholders = ",".join("?" * len(ALL_STAGES))
    raw = db.execute(
        f"""
        SELECT changed_at, new_value AS stage
        FROM audit_log
        WHERE field_changed = 'stage'
          AND changed_at >= ?
          AND new_value IN ({placeholders})
        """,
        (day_window_start_utc(_FUNNEL_WINDOW_DAYS), *ALL_STAGES),
    ).fetchall()

    daily = _build_daily_matrix(_bucket_by_local_day(raw), start_day, today)
    totals = {stage: sum(daily[d][stage] for d in daily) for stage in ALL_STAGES}

    total_scored = totals.get("scored", 0)
    conversions = {}
    for i, stage in enumerate(FUNNEL_STAGES[1:], 1):
        prev_stage = FUNNEL_STAGES[i - 1]
        prev_n = totals.get(prev_stage, 0)
        cur_n = totals.get(stage, 0)
        if min_n_gate(prev_n):
            pct, lo, hi = wilson_ci_pct(cur_n, prev_n)
            conversions[stage] = {"pct": pct, "lo": lo, "hi": hi, "n": prev_n, "gated": False}
        else:
            conversions[stage] = {"pct": 0, "lo": 0, "hi": 0, "n": prev_n, "gated": True}
    rejection_rate = None
    n_rejected = totals.get("rejected", 0)
    if min_n_gate(total_scored):
        pct, lo, hi = wilson_ci_pct(n_rejected, total_scored)
        rejection_rate = {"pct": pct, "lo": lo, "hi": hi, "n": total_scored, "gated": False}
    else:
        rejection_rate = {"pct": 0, "lo": 0, "hi": 0, "n": total_scored, "gated": True}

    markers = config_change_markers(db, start_day, today)

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
        "config_markers": markers,
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
            "conversions": conversions,
            "rejection_rate": rejection_rate,
            "chart_data_json": json.dumps(chart_data),
        },
    )


def _build_daily_matrix(
    rows: Sequence[sqlite3.Row | tuple[str, str, int]],
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
    today = today_local()
    window_start = today - timedelta(days=_FEEDBACK_WINDOW_DAYS - 1)
    week_start = today - timedelta(days=_FEEDBACK_WEEK_DAYS - 1)

    # Bucket feedback_log.created_at (naïve-UTC, defaults to datetime('now')) on
    # the operator's configured-TZ calendar day (#967). Fetch raw timestamps and group in
    # Python; the >= bound is local-midnight of window_start expressed in UTC.
    raw = db.execute(
        """
        SELECT created_at,
               COALESCE(NULLIF(reject_reason, ''), '(blank)') AS reason
        FROM feedback_log
        WHERE created_at >= ?
        """,
        (day_window_start_utc(_FEEDBACK_WINDOW_DAYS),),
    ).fetchall()
    daily_rows = _bucket_by_local_day(raw)

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

    reason_cis = {}
    for r in reasons:
        if min_n_gate(window_total):
            pct, lo, hi = wilson_ci_pct(window_totals[r], window_total)
            reason_cis[r] = {"pct": pct, "lo": lo, "hi": hi, "gated": False}
        else:
            reason_cis[r] = {"pct": 0, "lo": 0, "hi": 0, "gated": True}

    markers = config_change_markers(db, window_start, today)

    chart_data = {
        "labels": [d.isoformat() for d in sorted(daily)],
        "datasets": [
            {
                "label": r,
                "data": [daily[d][r] for d in sorted(daily)],
            }
            for r in reasons
        ],
        "config_markers": markers,
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
            "reason_cis": reason_cis,
            "daily_table": [
                {"day": d.isoformat(), **{r: daily[d][r] for r in reasons}} for d in sorted(daily, reverse=True)
            ],
            "chart_data_json": json.dumps(chart_data),
        },
    )


def _build_reason_matrix(
    rows: Sequence[sqlite3.Row | tuple[str, str, int]],
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
    today = today_local()
    start_day = today - timedelta(days=_SCORING_WINDOW_DAYS - 1)

    # Window bound is local-midnight of start_day expressed in UTC, so a
    # late-evening local scored transition isn't dropped a day early (#967). No day bucketing here —
    # this query is a DISTINCT membership set, not a daily series.
    scored_id_rows = db.execute(
        """
        SELECT DISTINCT job_id
        FROM audit_log
        WHERE field_changed = 'stage'
          AND new_value = 'scored'
          AND changed_at >= ?
        """,
        (day_window_start_utc(_SCORING_WINDOW_DAYS),),
    ).fetchall()
    scored_ids = [r["job_id"] if isinstance(r, sqlite3.Row) else r[0] for r in scored_id_rows]
    total_scored = len(scored_ids)

    histograms: dict[str, list[dict[str, int | str]]] = {}
    coverage: dict[str, dict[str, int]] = {}
    per_source: dict[str, dict[str, list[dict[str, int | str]]]] = {}

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

        source_rows = db.execute(
            f"""
            SELECT j.source, j.relevance_score AS v
            FROM jobs j
            WHERE j.id IN ({placeholders})
              AND j.relevance_score IS NOT NULL
              AND j.source IS NOT NULL AND j.source != ''
            """,
            scored_ids,
        ).fetchall()
        source_groups: dict[str, list] = {}
        for row in source_rows:
            src = row["source"] if isinstance(row, sqlite3.Row) else row[0]
            val = row["v"] if isinstance(row, sqlite3.Row) else row[1]
            source_groups.setdefault(src, []).append(val)

        for src, vals in sorted(source_groups.items(), key=lambda x: -len(x[1])):
            if min_n_gate(len(vals)):
                per_source.setdefault("relevance", {})[src] = _bucketize_scores(vals, "int_1_10")
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
            "per_source": per_source,
            "chart_data_json": json.dumps(chart_data),
            "per_source_json": json.dumps(per_source),
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

    reason_cis = {}
    for r in reasons:
        if min_n_gate(total_rejections):
            pct, lo, hi = wilson_ci_pct(global_totals[r], total_rejections)
            reason_cis[r] = {"pct": pct, "lo": lo, "hi": hi, "gated": False}
        else:
            reason_cis[r] = {"pct": 0, "lo": 0, "hi": 0, "gated": True}

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
            "reason_cis": reason_cis,
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

    stage_cis = {}
    for stage in _THROUGHPUT_STAGES:
        n = totals[stage]
        if min_n_gate(grand_total):
            pct, lo, hi = wilson_ci_pct(n, grand_total)
            stage_cis[stage] = {"pct": pct, "lo": lo, "hi": hi, "gated": False}
        else:
            stage_cis[stage] = {"pct": 0, "lo": 0, "hi": 0, "gated": True}

    markers = config_change_markers(db)

    chart_data = {
        "labels": weeks_sorted,
        "datasets": [
            {"label": stage, "data": [weekly[w][stage] for w in weeks_sorted]} for stage in _THROUGHPUT_STAGES
        ],
        "config_markers": markers,
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
            "stage_cis": stage_cis,
            "grand_total": grand_total,
            "chart_data_json": json.dumps(chart_data),
        },
    )


_EFFECTIVENESS_GHOST_DAYS = 21


@router.get("/stats/effectiveness", response_class=HTMLResponse)
def effectiveness(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Outcome tracking: apply-to-response rates, stratified."""
    applied_rows = db.execute(
        """
        SELECT j.id, j.fingerprint, j.company, j.source, j.company_tier,
               j.relevance_score, j.stage,
               MIN(a.changed_at) AS applied_at
        FROM jobs j
        JOIN audit_log a ON a.job_id = j.id
           AND a.field_changed = 'stage' AND a.new_value = 'applied'
        WHERE j.stage IN ('applied', 'interview', 'offer', 'not_selected', 'withdrawn')
        GROUP BY j.id
        """,
    ).fetchall()

    total_applied = len(applied_rows)

    interviews = sum(
        1 for r in applied_rows if (r["stage"] if isinstance(r, sqlite3.Row) else r[4]) in ("interview", "offer")
    )
    not_selected = sum(
        1 for r in applied_rows if (r["stage"] if isinstance(r, sqlite3.Row) else r[4]) == "not_selected"
    )

    ghost = 0
    for r in applied_rows:
        stage = r["stage"] if isinstance(r, sqlite3.Row) else r[4]
        if stage != "applied":
            continue
        applied_at = r["applied_at"] if isinstance(r, sqlite3.Row) else r[7]
        if not applied_at:
            continue
        try:
            applied_dt = datetime.fromisoformat(applied_at.replace(" ", "T"))
        except (ValueError, AttributeError):
            continue
        if (datetime.now(UTC) - applied_dt.replace(tzinfo=UTC)).days >= _EFFECTIVENESS_GHOST_DAYS:
            ghost += 1

    responded = interviews + not_selected
    if min_n_gate(total_applied):
        response_rate = wilson_ci_pct(responded, total_applied)
        interview_rate = wilson_ci_pct(interviews, total_applied)
        ghost_rate = wilson_ci_pct(ghost, total_applied)
    else:
        response_rate = (0.0, 0.0, 0.0)
        interview_rate = (0.0, 0.0, 0.0)
        ghost_rate = (0.0, 0.0, 0.0)

    by_source: dict[str, dict] = {}
    source_groups: dict[str, list] = {}
    for r in applied_rows:
        src = r["source"] if isinstance(r, sqlite3.Row) else r[3]
        if not src:
            continue
        source_groups.setdefault(src, []).append(r)
    for src, rows in sorted(source_groups.items(), key=lambda x: -len(x[1])):
        n = len(rows)
        src_interviews = sum(
            1 for r in rows if (r["stage"] if isinstance(r, sqlite3.Row) else r[4]) in ("interview", "offer")
        )
        if min_n_gate(n):
            pct, lo, hi = wilson_ci_pct(src_interviews, n)
            by_source[src] = {"n": n, "interviews": src_interviews, "pct": pct, "lo": lo, "hi": hi, "gated": False}
        else:
            by_source[src] = {"n": n, "interviews": src_interviews, "pct": 0, "lo": 0, "hi": 0, "gated": True}

    latency_days: list[int] = []
    for r in applied_rows:
        stage = r["stage"] if isinstance(r, sqlite3.Row) else r[4]
        if stage not in ("interview", "offer", "not_selected"):
            continue
        applied_at = r["applied_at"] if isinstance(r, sqlite3.Row) else r[7]
        if not applied_at:
            continue
        response_row = db.execute(
            """
            SELECT MIN(changed_at) AS resp_at FROM audit_log
            WHERE job_id = ? AND field_changed = 'stage'
              AND new_value IN ('interview', 'not_selected')
            """,
            (r["id"] if isinstance(r, sqlite3.Row) else r[0],),
        ).fetchone()
        if response_row and response_row[0]:
            try:
                resp_dt = datetime.fromisoformat(response_row[0].replace(" ", "T"))
                app_dt = datetime.fromisoformat(applied_at.replace(" ", "T"))
                latency_days.append((resp_dt - app_dt).days)
            except (ValueError, AttributeError):
                pass

    latency_stats = None
    if latency_days:
        latency_days.sort()
        latency_stats = {
            "median": latency_days[len(latency_days) // 2],
            "p25": latency_days[len(latency_days) // 4] if len(latency_days) >= 4 else latency_days[0],
            "p75": latency_days[3 * len(latency_days) // 4] if len(latency_days) >= 4 else latency_days[-1],
            "n": len(latency_days),
        }

    n_gated = not min_n_gate(total_applied)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="stats/effectiveness.html",
        context={
            "tab": "effectiveness",
            "total_applied": total_applied,
            "interviews": interviews,
            "not_selected": not_selected,
            "ghost": ghost,
            "ghost_days": _EFFECTIVENESS_GHOST_DAYS,
            "response_rate": {"pct": response_rate[0], "lo": response_rate[1], "hi": response_rate[2]},
            "interview_rate": {"pct": interview_rate[0], "lo": interview_rate[1], "hi": interview_rate[2]},
            "ghost_rate": {"pct": ghost_rate[0], "lo": ghost_rate[1], "hi": ghost_rate[2]},
            "by_source": by_source,
            "latency_stats": latency_stats,
            "n_gated": n_gated,
        },
    )


@router.get("/stats/recall-audit", response_class=HTMLResponse)
def recall_audit(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Recall-audit results — weekly upgrade-rate over time."""
    audit_rows = db.execute(
        """
        SELECT date(audited_at) AS day,
               COUNT(*) AS total,
               SUM(upgraded) AS upgrades,
               auditor_model
        FROM recall_audit
        GROUP BY day
        ORDER BY day DESC
        LIMIT 52
        """,
    ).fetchall()

    # One row per audit *date* (GROUP BY date(audited_at)), not per ISO week —
    # the cron runs weekly but the grain is per-run, so label it by date (#967).
    audits: list[dict] = []
    for row in audit_rows:
        day = row["day"] if isinstance(row, sqlite3.Row) else row[0]
        total = row["total"] if isinstance(row, sqlite3.Row) else row[1]
        upgrades = row["upgrades"] if isinstance(row, sqlite3.Row) else row[2]
        model = row["auditor_model"] if isinstance(row, sqlite3.Row) else row[3]
        if min_n_gate(total):
            pct, lo, hi = wilson_ci_pct(upgrades, total)
            gated = False
        else:
            pct, lo, hi = 0.0, 0.0, 0.0
            gated = True
        audits.append(
            {
                "date": day,
                "total": total,
                "upgrades": upgrades,
                "pct": pct,
                "lo": lo,
                "hi": hi,
                "gated": gated,
                "model": model,
                "alert": not gated and pct > 10.0,
            }
        )

    chart_data = {
        "labels": [a["date"] for a in reversed(audits)],
        "datasets": [
            {"label": "upgrade rate %", "data": [a["pct"] for a in reversed(audits)]},
        ],
    }

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="stats/recall_audit.html",
        context={
            "tab": "recall-audit",
            "audits": audits,
            "has_data": len(audits) > 0,
            "chart_data_json": json.dumps(chart_data),
        },
    )


@router.get("/stats/config-change/{change_date}", response_class=JSONResponse)
def config_change_detail(
    change_date: str,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> JSONResponse:
    """Before/after metrics for a specific config-change date (popover API)."""
    try:
        date.fromisoformat(change_date)
    except ValueError:
        return JSONResponse({"error": "invalid date"}, status_code=400)

    result = before_after_metrics(db, change_date)
    return JSONResponse(result)
