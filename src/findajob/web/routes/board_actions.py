"""Board action POST handlers — web write surface for 14c PR-A (#61).

One handler per operator action. Handlers are idempotent (the DB stage is
re-read before any write), return a re-rendered ``<tr>`` for HTMX
``outerHTML`` swap, and raise 404 on unknown fingerprint. Prep dispatch
launches ``prep_application.py`` via ``subprocess.Popen`` with
``start_new_session=True`` so the HTTP response returns immediately while
prep keeps running after the request finishes.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.paths import BASE
from findajob.utils import log_event, write_audit
from findajob.web.routes.board import _DASHBOARD_COLS
from findajob.web.routes.materials import get_db

router = APIRouter()

_DASHBOARD_ROW_SQL = (
    "SELECT fingerprint, title, company, location, remote_status, known_contacts, "
    "comp_estimate, ai_notes, relevance_score, interview_likelihood, "
    "stage, created_at, stage_updated FROM jobs WHERE fingerprint=?"
)


def _fetch_dashboard_row(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return db.execute(_DASHBOARD_ROW_SQL, (fingerprint,)).fetchone()


def _render_dashboard_row(request: Request, row: sqlite3.Row) -> HTMLResponse:
    """Render a single dashboard row for HTMX outerHTML swap."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_row.html",
        context={
            "columns": _DASHBOARD_COLS,
            "row": row,
            "tab": "dashboard",
            "materials_base_url": os.environ.get("FINDAJOB_MATERIALS_BASE_URL", ""),
        },
    )


@router.post("/board/jobs/{fingerprint}/prep", response_class=HTMLResponse)
def prep(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    row = _fetch_dashboard_row(db, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Idempotency: already in flight or already prepped — return current row unchanged.
    if row["stage"] in ("prep_in_progress", "materials_drafted"):
        return _render_dashboard_row(request, row)

    job = db.execute(
        "SELECT id, title, company, url, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()

    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE jobs SET stage='prep_in_progress', apply_flag=1, "
        "stage_updated=?, updated_at=? WHERE id=?",
        (now, now, job["id"]),
    )
    db.commit()
    write_audit(db, job["id"], "stage", job["stage"], "prep_in_progress")
    log_event(
        "web_prep_dispatched",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
    )

    subprocess.Popen(
        [
            sys.executable,
            f"{BASE}/scripts/prep_application.py",
            job["company"],
            job["title"],
            job["url"],
            job["id"],
            "--no-sync",
        ],
        start_new_session=True,
    )

    updated = _fetch_dashboard_row(db, fingerprint)
    assert updated is not None  # we just updated this row
    return _render_dashboard_row(request, updated)
