"""Board tabs: /board/dashboard, /applied, /review, /waitlist, /archive."""

from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from findajob.web.routes.materials import get_db

router = APIRouter()


_DASHBOARD_COLS = [
    ("Score", "fit_score"),
    ("Prob", "probability_score"),
    ("Rel", "relevance_score"),
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
_DASHBOARD_DEFAULT_SORT = "fit_score"

_DASHBOARD_WHERE = (
    "(fit_score >= 7 AND stage IN ('scored','manual_review')) "
    "OR stage IN ('prep_in_progress','materials_drafted')"
)


@router.get("/board/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _DASHBOARD_SORTABLE else _DASHBOARD_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    rows = db.execute(
        f"SELECT fingerprint, title, company, location, remote_status, known_contacts, "
        f"comp_estimate, ai_notes, fit_score, probability_score, relevance_score, "
        f"stage, created_at, stage_updated FROM jobs WHERE {_DASHBOARD_WHERE} "
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
_APPLIED_SORTABLE = {c for _, c in _APPLIED_COLS if c not in {"days_since_applied"}} | {
    "applied_date"
}
_APPLIED_DEFAULT_SORT = "applied_date"


@router.get("/board/applied", response_class=HTMLResponse)
def applied(
    request: Request,
    sort: str = Query(default=""),
    desc: int = Query(default=1),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    sort_col = sort if sort in _APPLIED_SORTABLE else _APPLIED_DEFAULT_SORT
    order = "DESC" if desc else "ASC"
    sql = f"""
    SELECT j.fingerprint, j.title, j.company, j.stage, j.location, j.remote_status,
           j.known_contacts, j.comp_estimate, j.ai_notes, j.user_notes, j.created_at,
           al.applied_date,
           CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied
    FROM jobs j
    LEFT JOIN (
      SELECT job_id, MIN(changed_at) AS applied_date
      FROM audit_log
      WHERE field_changed = 'stage' AND new_value = 'applied'
      GROUP BY job_id
    ) al ON al.job_id = j.fingerprint
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
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )
