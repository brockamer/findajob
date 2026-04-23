"""Board tabs: /board/dashboard, /applied, /review, /waitlist, /rejected, /archive."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from findajob.web.routes.materials import get_db

router = APIRouter()


def _filter_clause(q: str) -> tuple[str, list[str]]:
    """Build a case-insensitive LIKE filter against title + company.

    Returns ('', []) when q is empty — callers skip the filter entirely.
    Otherwise returns the SQL fragment (leading space, AND ...) and the
    two %q% params to bind.
    """
    if not q:
        return "", []
    like = f"%{q}%"
    return " AND (title LIKE ? COLLATE NOCASE OR company LIKE ? COLLATE NOCASE)", [like, like]


_DASHBOARD_COLS = [
    ("Rel", "relevance_score"),
    ("Fit", "fit_score"),
    ("Prob", "probability_score"),
    ("Likelihood", "interview_likelihood"),
    ("Title", "title"),
    ("Company", "company"),
    ("Location", "location"),
    ("Remote", "remote_status"),
    ("Contacts", "known_contacts"),
    ("Comp", "comp_estimate"),
    ("Notes", "ai_notes"),
    ("Date", "created_at"),
]

_DASHBOARD_SORTABLE = {c for _, c in _DASHBOARD_COLS}
_DASHBOARD_DEFAULT_SORT = "relevance_score"

_VALID_DENSITIES = {"compact", "expanded"}
_DEFAULT_DENSITY = "compact"


def _normalize_density(raw: str) -> str:
    return raw if raw in _VALID_DENSITIES else _DEFAULT_DENSITY


# Exclude rows whose (company, title) already has a sibling in a post-application
# stage — dedup failures (see #13/#16/#17) otherwise surface already-applied jobs.
# LOWER+TRIM guards against whitespace/casing differences in title ingestion.
_DASHBOARD_WHERE = (
    "((relevance_score >= 7 AND stage IN ('scored','manual_review'))"
    " OR stage IN ('prep_in_progress','materials_drafted'))"
    " AND NOT EXISTS ("
    "  SELECT 1 FROM jobs sib"
    "  WHERE sib.id != jobs.id"
    "    AND LOWER(TRIM(sib.company)) = LOWER(TRIM(jobs.company))"
    "    AND LOWER(TRIM(sib.title)) = LOWER(TRIM(jobs.title))"
    "    AND sib.stage IN ('applied','interview','offer','not_selected')"
    " )"
)


@router.get("/board/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _DASHBOARD_SORTABLE else _DASHBOARD_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    rows = db.execute(
        f"SELECT fingerprint, title, company, location, remote_status, known_contacts, "
        f"comp_estimate, ai_notes, relevance_score, fit_score, probability_score, "
        f"interview_likelihood, stage, created_at, stage_updated, url, prep_folder_path "
        f"FROM jobs WHERE {_DASHBOARD_WHERE} "
        f"ORDER BY {sort_col} {order}"
    ).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/dashboard.html",
        context={
            "columns": _DASHBOARD_COLS,
            "rows": rows,
            "sort": sort_col,
            "desc": desc,
            "density": _normalize_density(density),
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
        },
    )


_APPLIED_COLS = [
    ("Title", "title"),
    ("Company", "company"),
    ("Applied", "applied_date"),
    ("Days", "days_since_applied"),
    ("Stage", "stage"),
    ("Notes", "user_notes"),
    ("Contacts", "known_contacts"),
    ("Location", "location"),
    ("Remote", "remote_status"),
    ("Comp", "comp_estimate"),
    ("AI notes", "ai_notes"),
]
_APPLIED_SORTABLE = {c for _, c in _APPLIED_COLS} | {"applied_date"}
_APPLIED_DEFAULT_SORT = "applied_date"


@router.get("/board/applied", response_class=HTMLResponse)
def applied(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _APPLIED_SORTABLE else _APPLIED_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    # applied_date = earliest audit_log transition into a post-application stage.
    # Mirrors scripts/sync_sheet.py — jobs can skip 'applied' (recruiter flows go
    # straight to 'interview'), and audit_log.job_id stores jobs.id (UUID), not
    # jobs.fingerprint.
    sql = f"""
    SELECT j.fingerprint, j.title, j.company, j.stage, j.location, j.remote_status,
           j.known_contacts, j.comp_estimate, j.ai_notes, j.user_notes, j.created_at,
           j.url,
           al.applied_date,
           CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied
    FROM jobs j
    LEFT JOIN (
      SELECT job_id, MIN(changed_at) AS applied_date
      FROM audit_log
      WHERE field_changed = 'stage' AND new_value IN ('applied','interview','offer')
      GROUP BY job_id
    ) al ON al.job_id = j.id
    WHERE j.stage IN ('applied','interview','offer')
    ORDER BY {sort_col} {order}
    """
    rows = db.execute(sql).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/applied.html",
        context={
            "columns": _APPLIED_COLS,
            "rows": rows,
            "sort": sort_col,
            "desc": desc,
            "density": _normalize_density(density),
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )


_REVIEW_COLS = [
    ("Title", "title"),
    ("Company", "company"),
    ("Flag reason", "score_flag_reason"),
    ("Source", "source"),
    ("Date", "created_at"),
]
_REVIEW_SORTABLE = {c for _, c in _REVIEW_COLS}
_REVIEW_DEFAULT_SORT = "created_at"


@router.get("/board/review", response_class=HTMLResponse)
def review(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _REVIEW_SORTABLE else _REVIEW_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    rows = db.execute(
        f"SELECT fingerprint, title, company, score_flag_reason, source, created_at, stage, url "
        f"FROM jobs WHERE stage = 'manual_review' ORDER BY {sort_col} {order}"
    ).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/review.html",
        context={
            "columns": _REVIEW_COLS,
            "rows": rows,
            "sort": sort_col,
            "desc": desc,
            "density": _normalize_density(density),
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )


_WAITLIST_COLS = [
    ("Title", "title"),
    ("Company", "company"),
    ("Rel", "relevance_score"),
    ("Location", "location"),
    ("Remote", "remote_status"),
    ("AI notes", "ai_notes"),
    ("Date", "created_at"),
    ("Blocking app", "blocking_app"),
]
_WAITLIST_SORTABLE = {c for _, c in _WAITLIST_COLS if c != "blocking_app"}
_WAITLIST_DEFAULT_SORT = "created_at"


@router.get("/board/waitlist", response_class=HTMLResponse)
def waitlist(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _WAITLIST_SORTABLE else _WAITLIST_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    sql = f"""
    SELECT w.fingerprint, w.title, w.company, w.relevance_score, w.location, w.remote_status,
           w.ai_notes, w.created_at, w.stage, w.url,
           (SELECT j2.title || ' (' || j2.stage || ')'
              FROM jobs j2
             WHERE j2.company = w.company
               AND j2.fingerprint != w.fingerprint
               AND j2.stage IN ('applied','interview','offer','materials_drafted','prep_in_progress')
             ORDER BY j2.stage_updated DESC
             LIMIT 1) AS blocking_app
    FROM jobs w
    WHERE w.stage = 'waitlisted'
    ORDER BY {sort_col} {order}
    """
    rows = db.execute(sql).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/waitlist.html",
        context={
            "columns": _WAITLIST_COLS,
            "rows": rows,
            "sort": sort_col,
            "desc": desc,
            "density": _normalize_density(density),
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )


_REJECTED_COLS = [
    ("Title", "title"),
    ("Company", "company"),
    ("Reason", "reject_reason"),
    ("Rejected", "rejected_date"),
    ("Source", "rejection_source"),
]
_REJECTED_SORTABLE = {c for _, c in _REJECTED_COLS}
_REJECTED_DEFAULT_SORT = "rejected_date"


# Latest stage-transition into rejected/not_selected per job. audit_log.job_id
# stores jobs.id (UUID) — match jobs.id, not fingerprint. MAX(changed_at) picks
# the most recent transition in case a job was rejected, reactivated, and
# rejected again. See CLAUDE.md §"audit_log timestamp format".
_REJECTED_SQL = """
SELECT j.fingerprint, j.title, j.company, j.url, j.stage, j.reject_reason,
       CASE j.stage WHEN 'not_selected' THEN 'company' ELSE 'user' END AS rejection_source,
       al.rejected_date
FROM jobs j
LEFT JOIN (
  SELECT job_id, MAX(changed_at) AS rejected_date
  FROM audit_log
  WHERE field_changed = 'stage' AND new_value IN ('rejected','not_selected')
  GROUP BY job_id
) al ON al.job_id = j.id
WHERE j.stage IN ('rejected','not_selected')
ORDER BY {sort_col} {order}
"""


@router.get("/board/rejected", response_class=HTMLResponse)
def rejected(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _REJECTED_SORTABLE else _REJECTED_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    rows = db.execute(_REJECTED_SQL.format(sort_col=sort_col, order=order)).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/rejected.html",
        context={
            "columns": _REJECTED_COLS,
            "rows": rows,
            "sort": sort_col,
            "desc": desc,
            "density": _normalize_density(density),
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/rejected/rows", response_class=HTMLResponse)
def rejected_rows(
    request: Request,
    q: str = Query(default=""),
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _REJECTED_SORTABLE else _REJECTED_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    filter_sql, params = _filter_clause(q)
    qualified_filter = filter_sql.replace("title", "j.title").replace("company", "j.company")
    base = _REJECTED_SQL.format(sort_col=sort_col, order=order)
    sql = base.replace(
        "WHERE j.stage IN ('rejected','not_selected')",
        f"WHERE j.stage IN ('rejected','not_selected'){qualified_filter}",
    )
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "columns": _REJECTED_COLS,
            "rows": rows,
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )


_ARCHIVE_COLS = [
    ("Score", "fit_score"),
    ("Title", "title"),
    ("Company", "company"),
    ("Stage", "stage"),
    ("Location", "location"),
    ("Remote", "remote_status"),
    ("Date", "created_at"),
    ("Source", "source"),
    ("URL", "url"),
]
_ARCHIVE_SORTABLE = {c for _, c in _ARCHIVE_COLS}
_ARCHIVE_DEFAULT_SORT = "created_at"
_ARCHIVE_PAGE_SIZE = 100


def _archive_select_sql(sort_col: str, order: str) -> str:
    return (
        "SELECT fingerprint, title, company, stage, fit_score, location, remote_status, "
        "source, url, created_at, stage_updated "
        f"FROM jobs ORDER BY {sort_col} {order} LIMIT ? OFFSET ?"
    )


@router.get("/board/archive", response_class=HTMLResponse)
def archive(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _ARCHIVE_SORTABLE else _ARCHIVE_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    rows = db.execute(_archive_select_sql(sort_col, order), (_ARCHIVE_PAGE_SIZE, 0)).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/archive.html",
        context={
            "columns": _ARCHIVE_COLS,
            "rows": rows,
            "sort": sort_col,
            "desc": desc,
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
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _ARCHIVE_SORTABLE else _ARCHIVE_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    rows = db.execute(_archive_select_sql(sort_col, order), (_ARCHIVE_PAGE_SIZE, offset)).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_archive_rows.html",
        context={
            "columns": _ARCHIVE_COLS,
            "rows": rows,
            "tab": "archive",
            "next_offset": offset + _ARCHIVE_PAGE_SIZE if has_more else None,
            "sort": sort_col,
            "desc": desc,
            "materials_base_url": materials_base_url,
        },
    )


# ──────────────────────────────────────────────────────────────────────
# HTMX filter endpoints — each tab renders only its <tbody> rows as
# _job_rows_fragment.html. Shared ?q= text filters title + company.
# ──────────────────────────────────────────────────────────────────────


@router.get("/board/dashboard/rows", response_class=HTMLResponse)
def dashboard_rows(
    request: Request,
    q: str = Query(default=""),
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _DASHBOARD_SORTABLE else _DASHBOARD_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    filter_sql, params = _filter_clause(q)
    rows = db.execute(
        f"SELECT fingerprint, title, company, location, remote_status, known_contacts, "
        f"comp_estimate, ai_notes, relevance_score, fit_score, probability_score, "
        f"interview_likelihood, stage, created_at, stage_updated, url, prep_folder_path "
        f"FROM jobs WHERE ({_DASHBOARD_WHERE}) {filter_sql} "
        f"ORDER BY {sort_col} {order}",
        params,
    ).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "columns": _DASHBOARD_COLS,
            "rows": rows,
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/applied/rows", response_class=HTMLResponse)
def applied_rows(
    request: Request,
    q: str = Query(default=""),
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _APPLIED_SORTABLE else _APPLIED_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    filter_sql, params = _filter_clause(q)
    qualified_filter = filter_sql.replace("title", "j.title").replace("company", "j.company")
    sql = f"""
    SELECT j.fingerprint, j.title, j.company, j.stage, j.location, j.remote_status,
           j.known_contacts, j.comp_estimate, j.ai_notes, j.user_notes, j.created_at,
           j.url,
           al.applied_date,
           CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied
    FROM jobs j
    LEFT JOIN (
      SELECT job_id, MIN(changed_at) AS applied_date
      FROM audit_log
      WHERE field_changed = 'stage' AND new_value IN ('applied','interview','offer')
      GROUP BY job_id
    ) al ON al.job_id = j.id
    WHERE j.stage IN ('applied','interview','offer'){qualified_filter}
    ORDER BY {sort_col} {order}
    """
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "columns": _APPLIED_COLS,
            "rows": rows,
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/review/rows", response_class=HTMLResponse)
def review_rows(
    request: Request,
    q: str = Query(default=""),
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _REVIEW_SORTABLE else _REVIEW_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    filter_sql, params = _filter_clause(q)
    rows = db.execute(
        f"SELECT fingerprint, title, company, score_flag_reason, source, created_at, stage, url "
        f"FROM jobs WHERE stage = 'manual_review' {filter_sql} "
        f"ORDER BY {sort_col} {order}",
        params,
    ).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "columns": _REVIEW_COLS,
            "rows": rows,
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/waitlist/rows", response_class=HTMLResponse)
def waitlist_rows(
    request: Request,
    q: str = Query(default=""),
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _WAITLIST_SORTABLE else _WAITLIST_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    filter_sql, params = _filter_clause(q)
    qualified_filter = filter_sql.replace("title", "w.title").replace("company", "w.company")
    sql = f"""
    SELECT w.fingerprint, w.title, w.company, w.relevance_score, w.location, w.remote_status,
           w.ai_notes, w.created_at, w.stage, w.url,
           (SELECT j2.title || ' (' || j2.stage || ')'
              FROM jobs j2
             WHERE j2.company = w.company
               AND j2.fingerprint != w.fingerprint
               AND j2.stage IN ('applied','interview','offer','materials_drafted','prep_in_progress')
             ORDER BY j2.stage_updated DESC
             LIMIT 1) AS blocking_app
    FROM jobs w
    WHERE w.stage = 'waitlisted'{qualified_filter}
    ORDER BY {sort_col} {order}
    """
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "columns": _WAITLIST_COLS,
            "rows": rows,
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )
