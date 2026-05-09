"""Board tabs: /board/dashboard, /applied, /review, /waitlist, /rejected, /archive."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from findajob.web.company_history import build_history_by_fp, fetch_company_history
from findajob.web.discoveries import load_discoveries_summary
from findajob.web.filters import ColumnSpec, ParsedFilters, build_filter_clauses, parse_filter_params
from findajob.web.filters import registry as filter_registry
from findajob.web.routes.materials import get_db

router = APIRouter()


_VALID_DENSITIES = {"compact", "expanded"}
_DEFAULT_DENSITY = "compact"


def _normalize_density(raw: str) -> str:
    return raw if raw in _VALID_DENSITIES else _DEFAULT_DENSITY


def _resolve_visible(specs: tuple[ColumnSpec, ...], parsed: ParsedFilters) -> set[str]:
    """Cascade: URL ?cols= > ColumnSpec.default_visible. (Persisted prefs in #277.)"""
    if parsed.cols:
        return set(parsed.cols)
    return {s.name for s in specs if s.default_visible}


_DASHBOARD_DEFAULT_SORT = "relevance_score"
_DASHBOARD_DEFAULT_SCORE_MIN = 7

# Stage gate + dedup-sibling exclusion. The score floor is no longer baked in —
# it's applied as a ROUTE-LEVEL DEFAULT (see _dashboard_query) so that
# ?relevance_score_min=5 actually surfaces score-5/6 buried gems instead of
# being clobbered by the base WHERE. The default keeps cold-load behavior
# unchanged at score >= 7.
_DASHBOARD_BASE_WHERE = (
    "stage IN ('scored','manual_review','prep_in_progress','materials_drafted')"
    " AND NOT EXISTS ("
    "  SELECT 1 FROM jobs sib"
    "  WHERE sib.id != jobs.id"
    "    AND LOWER(TRIM(sib.company)) = LOWER(TRIM(jobs.company))"
    "    AND LOWER(TRIM(sib.title)) = LOWER(TRIM(jobs.title))"
    "    AND sib.stage IN ('applied','interview','offer','not_selected')"
    " )"
)


def _apply_dashboard_default_score(parsed: ParsedFilters) -> ParsedFilters:
    """If the user didn't pass ?relevance_score_min/_max, apply a default score
    floor of 7 so the cold-load surface stays at 7+. Any explicit user value
    wins (including 0, e.g., ?relevance_score_min=0 to see everything)."""
    if "relevance_score" in parsed.numeric_range:
        return parsed
    from dataclasses import replace

    return replace(
        parsed,
        numeric_range={
            **parsed.numeric_range,
            "relevance_score": (_DASHBOARD_DEFAULT_SCORE_MIN, None),
        },
    )


def _dashboard_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = _apply_dashboard_default_score(parsed)
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _DASHBOARD_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT fingerprint, title, company, location, remote_status, known_contacts, "
        "comp_estimate, ai_notes, relevance_score, fit_score, probability_score, "
        "interview_likelihood, stage, created_at, stage_updated, url, prep_folder_path "
        f"FROM jobs WHERE ({_DASHBOARD_BASE_WHERE}){clauses} ORDER BY {sort} {order}"
    )
    return sql, params


@router.get("/board/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _dashboard_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    discoveries = load_discoveries_summary(request.app.state.base_root)
    rejections_pending = _rejections_pending_count(db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/dashboard.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "density": _normalize_density(density),
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
            "discoveries": discoveries,
            "rejections_pending": rejections_pending,
        },
    )


def _rejections_pending_count(db: sqlite3.Connection) -> int:
    """Pending rejection-review queue count for the dashboard widget (#362).

    Tolerates pre-#362 stacks where ``rejection_suggestions`` doesn't exist
    yet by returning 0 — same shape as the analogous notifications guard.
    """
    try:
        return int(db.execute("SELECT COUNT(*) FROM rejection_suggestions WHERE user_action = 'pending'").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


_APPLIED_DEFAULT_SORT = "applied_date"
_APPLIED_BASE_WHERE = "j.stage IN ('applied','interview','offer')"


def _applied_source() -> str:
    """FROM/JOIN clause for Applied — LEFT JOIN audit_log for applied_date."""
    return (
        "FROM jobs j "
        "LEFT JOIN ("
        "  SELECT job_id, MIN(changed_at) AS applied_date "
        "  FROM audit_log "
        "  WHERE field_changed = 'stage' AND new_value IN ('applied','interview','offer') "
        "  GROUP BY job_id"
        ") al ON al.job_id = j.id"
    )


def _applied_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.APPLIED_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _APPLIED_DEFAULT_SORT
    # sort by spec name → use db_expr if defined; else the bare name.
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else _APPLIED_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT j.fingerprint, j.title, j.company, j.stage, j.location, j.remote_status, "
        "       j.known_contacts, j.comp_estimate, j.ai_notes, j.user_notes, j.created_at, "
        "       j.url, "
        "       al.applied_date, "
        "       CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied, "
        "       (SELECT SUM(cl.cost_usd) FROM cost_log cl "
        "        WHERE cl.job_id = j.id AND cl.cost_usd IS NOT NULL) AS cost "
        f"{_applied_source()} "
        f"WHERE ({_APPLIED_BASE_WHERE}){clauses} "
        f"ORDER BY {sort_ref} {order}"
    )
    return sql, params


@router.get("/board/applied", response_class=HTMLResponse)
def applied(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.APPLIED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _applied_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/applied.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )


_REVIEW_DEFAULT_SORT = "created_at"
_REVIEW_BASE_WHERE = "stage = 'manual_review'"


def _review_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.REVIEW_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _REVIEW_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT fingerprint, title, company, score_flag_reason, source, created_at, stage, url "
        f"FROM jobs WHERE ({_REVIEW_BASE_WHERE}){clauses} "
        f"ORDER BY {sort} {order}"
    )
    return sql, params


@router.get("/board/review", response_class=HTMLResponse)
def review(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REVIEW_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _review_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/review.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )


_WAITLIST_DEFAULT_SORT = "w.created_at"
_WAITLIST_BASE_WHERE = "w.stage = 'waitlisted'"


def _waitlist_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.WAITLIST_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or "created_at"
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else _WAITLIST_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = f"""
    SELECT w.fingerprint, w.title, w.company, w.relevance_score,
           w.fit_score, w.probability_score, w.interview_likelihood,
           w.location, w.remote_status,
           w.ai_notes, w.created_at, w.stage, w.url,
           (SELECT j2.title || ' (' || j2.stage || ')'
              FROM jobs j2
             WHERE j2.company = w.company
               AND j2.fingerprint != w.fingerprint
               AND j2.stage IN ('applied','interview','offer','materials_drafted','prep_in_progress')
             ORDER BY j2.stage_updated DESC
             LIMIT 1) AS blocking_app
    FROM jobs w
    WHERE ({_WAITLIST_BASE_WHERE}){clauses}
    ORDER BY {sort_ref} {order}
    """
    return sql, params


@router.get("/board/waitlist", response_class=HTMLResponse)
def waitlist(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.WAITLIST_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _waitlist_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/waitlist.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "density": _normalize_density(density),
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )


_REJECTED_DEFAULT_SORT = "rejected_date"
_REJECTED_BASE_WHERE = "j.stage IN ('rejected','not_selected')"


def _rejected_source() -> str:
    # Latest stage-transition into rejected/not_selected per job. audit_log.job_id
    # stores jobs.id (UUID) — match jobs.id, not fingerprint. MAX(changed_at) picks
    # the most recent transition in case a job was rejected, reactivated, and
    # rejected again. See CLAUDE.md §"audit_log timestamp format".
    return (
        "FROM jobs j "
        "LEFT JOIN ("
        "  SELECT job_id, MAX(changed_at) AS rejected_date "
        "  FROM audit_log "
        "  WHERE field_changed = 'stage' AND new_value IN ('rejected','not_selected') "
        "  GROUP BY job_id"
        ") al ON al.job_id = j.id"
    )


def _rejected_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.REJECTED_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _REJECTED_DEFAULT_SORT
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else "al.rejected_date"
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT j.fingerprint, j.title, j.company, j.url, j.stage, j.reject_reason, "
        "       CASE j.stage WHEN 'not_selected' THEN 'company' ELSE 'user' END AS rejection_source, "
        "       al.rejected_date "
        f"{_rejected_source()} "
        f"WHERE ({_REJECTED_BASE_WHERE}){clauses} "
        f"ORDER BY {sort_ref} {order}"
    )
    return sql, params


@router.get("/board/rejected", response_class=HTMLResponse)
def rejected(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REJECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _rejected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/rejected.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/rejected/rows", response_class=HTMLResponse)
def rejected_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REJECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _rejected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )


_ARCHIVE_DEFAULT_SORT = "created_at"
_ARCHIVE_PAGE_SIZE = 100


def _archive_query(parsed: ParsedFilters, offset: int, page_size: int = _ARCHIVE_PAGE_SIZE) -> tuple[str, list[object]]:
    specs = filter_registry.ARCHIVE_COLUMNS
    clauses, filter_params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _ARCHIVE_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    # Strip leading " AND " and prefix with " WHERE " — Archive has no base WHERE.
    where_sql = ""
    if clauses:
        where_sql = " WHERE " + clauses[len(" AND ") :]
    sql = (
        "SELECT fingerprint, title, company, stage, relevance_score, fit_score, "
        "probability_score, location, remote_status, source, url, created_at, stage_updated "
        f"FROM jobs{where_sql} ORDER BY {sort} {order} LIMIT ? OFFSET ?"
    )
    params: list[object] = [*filter_params, page_size, offset]
    return sql, params


@router.get("/board/archive", response_class=HTMLResponse)
def archive(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.ARCHIVE_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _archive_query(parsed, offset=0)
    rows = db.execute(sql, params).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/archive.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "archive",
            "next_offset": _ARCHIVE_PAGE_SIZE if has_more else None,
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/archive/rows", response_class=HTMLResponse)
def archive_rows(
    request: Request,
    offset: int = Query(default=0),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.ARCHIVE_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _archive_query(parsed, offset=offset)
    rows = db.execute(sql, params).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_archive_rows.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "tab": "archive",
            "next_offset": offset + _ARCHIVE_PAGE_SIZE if has_more else None,
            "materials_base_url": materials_base_url,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# HTMX rows endpoints — each tab renders only its <tbody> rows as
# _job_rows_fragment.html, driven by the filter framework.
# ──────────────────────────────────────────────────────────────────────


@router.get("/board/dashboard/rows", response_class=HTMLResponse)
def dashboard_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _dashboard_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/applied/rows", response_class=HTMLResponse)
def applied_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.APPLIED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _applied_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/review/rows", response_class=HTMLResponse)
def review_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REVIEW_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _review_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/waitlist/rows", response_class=HTMLResponse)
def waitlist_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.WAITLIST_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _waitlist_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )
