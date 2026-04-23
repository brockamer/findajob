"""Landing page at / and placeholder groups."""

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


_PLACEHOLDERS = [
    # /ingest/ promoted to a real route in src/findajob/web/routes/ingest.py (#62).
    # /config/ promoted to a real route in src/findajob/web/routes/config.py (#149).
    # /tools/ promoted to a stub in src/findajob/web/routes/tools.py (#149).
    ("/docs/", "Docs", "User-facing documentation.", ""),
]


def _make_placeholder(path: str, label: str, hint: str, issue: str):
    @router.get(path, response_class=HTMLResponse)
    def _handler(request: Request) -> HTMLResponse:
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="placeholders/coming_soon.html",
            context={"label": label, "hint": hint, "issue": issue},
        )

    _handler.__name__ = f"placeholder_{label.lower()}"
    return _handler


for _p, _l, _h, _i in _PLACEHOLDERS:
    _make_placeholder(_p, _l, _h, _i)
