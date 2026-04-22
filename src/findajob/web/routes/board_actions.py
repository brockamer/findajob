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
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.actions import notify_waitlist_resurface
from findajob.paths import BASE
from findajob.utils import log_event, write_audit
from findajob.web.routes.board import _APPLIED_COLS, _DASHBOARD_COLS
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


_APPLIED_ROW_SQL = """
    SELECT j.fingerprint, j.title, j.company, j.stage, j.location, j.remote_status,
           j.known_contacts, j.comp_estimate, j.ai_notes, j.user_notes, j.created_at,
           al.applied_date,
           CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied
    FROM jobs j
    LEFT JOIN (
      SELECT job_id, MIN(changed_at) AS applied_date
      FROM audit_log
      WHERE field_changed = 'stage' AND new_value IN ('applied','interview','offer')
      GROUP BY job_id
    ) al ON al.job_id = j.id
    WHERE j.fingerprint = ?
"""


def _fetch_applied_row(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return db.execute(_APPLIED_ROW_SQL, (fingerprint,)).fetchone()


def _render_applied_row(request: Request, row: sqlite3.Row) -> HTMLResponse:
    """Render a single Applied-tab row for HTMX outerHTML swap."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_row.html",
        context={
            "columns": _APPLIED_COLS,
            "row": row,
            "tab": "applied",
            "materials_base_url": os.environ.get("FINDAJOB_MATERIALS_BASE_URL", ""),
        },
    )


def _transition_stage(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    new_stage: str,
    event_name: str,
) -> None:
    """Apply a plain stage transition: UPDATE, audit, log. No folder work."""
    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE jobs SET stage=?, stage_updated=?, updated_at=? WHERE id=?",
        (new_stage, now, now, job["id"]),
    )
    db.commit()
    write_audit(db, job["id"], "stage", job["stage"], new_stage)
    log_event(
        event_name,
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        stage=new_stage,
    )


def _move_folder_to_applied(db: sqlite3.Connection, job: sqlite3.Row) -> bool:
    """Move a prep folder from companies/ to companies/_applied/.

    Mirrors poll_flags.py's Applied-branch behavior. Returns True if a folder
    was actually moved.
    """
    jd = db.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd else None
    if not folder or not os.path.isdir(folder):
        return False
    applied_dir = os.path.join(BASE, "companies", "_applied")
    os.makedirs(applied_dir, exist_ok=True)
    dest = os.path.join(applied_dir, os.path.basename(folder))
    shutil.move(folder, dest)
    db.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
    db.commit()
    log_event("folder_moved_to_applied", job_id=job["id"], folder=os.path.basename(folder))
    return True


def _fetch_job(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT id, fingerprint, title, company, url, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()


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


@router.post("/board/jobs/{fingerprint}/apply", response_class=HTMLResponse)
def apply(
    fingerprint: str,
    request: Request,  # noqa: ARG001 — kept for handler signature parity
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Move job to the Applied tab. Returns empty body — HTMX removes the dashboard row."""
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] == "applied":
        return HTMLResponse("")
    _transition_stage(db, job, "applied", event_name="web_applied")
    _move_folder_to_applied(db, job)
    return HTMLResponse("")


@router.post("/board/jobs/{fingerprint}/interview", response_class=HTMLResponse)
def interview(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "interview":
        _transition_stage(db, job, "interview", event_name="web_interview")
    updated = _fetch_applied_row(db, fingerprint)
    assert updated is not None
    return _render_applied_row(request, updated)


@router.post("/board/jobs/{fingerprint}/offer", response_class=HTMLResponse)
def offer(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "offer":
        _transition_stage(db, job, "offer", event_name="web_offer")
    updated = _fetch_applied_row(db, fingerprint)
    assert updated is not None
    return _render_applied_row(request, updated)


@router.post("/board/jobs/{fingerprint}/withdraw", response_class=HTMLResponse)
def withdraw(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Withdraw from the application. Returns empty — row drops off Applied."""
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] == "withdrawn":
        return HTMLResponse("")
    _transition_stage(db, job, "withdrawn", event_name="web_withdrawn")
    notify_waitlist_resurface(db, job["company"])
    return HTMLResponse("")
