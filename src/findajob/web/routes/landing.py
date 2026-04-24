"""Landing page at /."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from findajob.web.routes.materials import get_db

router = APIRouter()


_STAGES_ORDER = [
    "scored",
    "manual_review",
    "prep_in_progress",
    "materials_drafted",
    "applied",
    "interview",
    "offer",
    "waitlisted",
    "rejected",
    "not_selected",
]


@router.get("/", response_class=HTMLResponse)
def landing(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    rows = db.execute("SELECT stage, COUNT(*) AS n FROM jobs GROUP BY stage").fetchall()
    counts = {r["stage"]: r["n"] for r in rows}
    ordered = [(s, counts.get(s, 0)) for s in _STAGES_ORDER]
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"ordered": ordered},
    )
