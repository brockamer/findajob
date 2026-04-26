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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.actions import (
    handle_not_selected,
    handle_reactivate,
    handle_rejection,
    handle_waitlist,
    notify_waitlist_resurface,
    promote_to_scored,
)
from findajob.paths import BASE
from findajob.utils import log_event, write_audit
from findajob.web.company_history import build_history_by_fp, fetch_company_history
from findajob.web.filters import registry as filter_registry
from findajob.web.routes.materials import get_db

router = APIRouter()

MAX_CONCURRENT_PREPS = 3
"""Upper bound on simultaneously-running prep subprocesses.

Mirrors scripts/poll_flags.py's cap. Keeps LLM-API spending bounded when the
operator mass-flags a morning's worth of jobs. When the cap is reached,
/prep and /regenerate return 429; the dashboard row stays actionable so the
operator can retry in a few minutes.
"""


def _prep_in_flight(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM jobs WHERE stage='prep_in_progress'").fetchone()[0]


def _prep_queue_full_response() -> HTMLResponse:
    return HTMLResponse(
        f"Prep queue full ({MAX_CONCURRENT_PREPS} in flight). Try again in a few minutes.",
        status_code=429,
    )


def _launch_prep_subprocess(job: sqlite3.Row) -> None:
    subprocess.Popen(
        [
            sys.executable,
            f"{BASE}/scripts/prep_application.py",
            job["company"],
            job["title"],
            job["url"],
            job["id"],
        ],
        start_new_session=True,
    )


_DASHBOARD_ROW_SQL = (
    "SELECT fingerprint, title, company, location, remote_status, known_contacts, "
    "comp_estimate, ai_notes, relevance_score, fit_score, probability_score, "
    "interview_likelihood, stage, created_at, stage_updated, url, prep_folder_path "
    "FROM jobs WHERE fingerprint=?"
)


def _fetch_dashboard_row(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return db.execute(_DASHBOARD_ROW_SQL, (fingerprint,)).fetchone()


def _render_dashboard_row(request: Request, row: sqlite3.Row, db: sqlite3.Connection) -> HTMLResponse:
    """Render a single dashboard row for HTMX outerHTML swap.

    Annotates the row with its company-history cell (#234) so the HTMX
    swap doesn't erase the history column until the next full-page reload.
    """
    templates = request.app.state.templates
    history_by_fp = build_history_by_fp([row], fetch_company_history(db))
    specs = filter_registry.DASHBOARD_COLUMNS
    visible = {s.name for s in specs if s.default_visible}
    return templates.TemplateResponse(
        request=request,
        name="_job_row.html",
        context={
            "specs": specs,
            "visible": visible,
            "row": row,
            "history_by_fp": history_by_fp,
            "tab": "dashboard",
            "materials_base_url": os.environ.get("FINDAJOB_MATERIALS_BASE_URL", ""),
        },
    )


_APPLIED_ROW_SQL = """
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
    WHERE j.fingerprint = ?
"""


def _fetch_applied_row(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return db.execute(_APPLIED_ROW_SQL, (fingerprint,)).fetchone()


def _render_applied_row(request: Request, row: sqlite3.Row) -> HTMLResponse:
    """Render a single Applied-tab row for HTMX outerHTML swap."""
    templates = request.app.state.templates
    specs = filter_registry.APPLIED_COLUMNS
    visible = {s.name for s in specs if s.default_visible}
    return templates.TemplateResponse(
        request=request,
        name="_job_row.html",
        context={
            "specs": specs,
            "visible": visible,
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

    Returns True if a folder was actually moved.
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
        return _render_dashboard_row(request, row, db)

    if _prep_in_flight(db) >= MAX_CONCURRENT_PREPS:
        return _prep_queue_full_response()

    job = db.execute(
        "SELECT id, title, company, url, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()

    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE jobs SET stage='prep_in_progress', apply_flag=1, stage_updated=?, updated_at=? WHERE id=?",
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

    _launch_prep_subprocess(job)

    updated = _fetch_dashboard_row(db, fingerprint)
    assert updated is not None  # we just updated this row
    return _render_dashboard_row(request, updated, db)


@router.post("/board/jobs/{fingerprint}/regenerate", response_class=HTMLResponse)
def regenerate(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Delete the existing prep folder and re-run prep from scratch."""
    row = _fetch_dashboard_row(db, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Idempotency: already running — don't clobber a live prep subprocess.
    if row["stage"] == "prep_in_progress":
        return _render_dashboard_row(request, row, db)

    if _prep_in_flight(db) >= MAX_CONCURRENT_PREPS:
        return _prep_queue_full_response()

    job = db.execute(
        "SELECT id, title, company, url, stage, prep_folder_path FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()

    folder = job["prep_folder_path"]
    if folder and os.path.isdir(folder):
        shutil.rmtree(folder)
        log_event("folder_removed_for_regen", job_id=job["id"], folder=os.path.basename(folder))

    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE jobs SET stage='prep_in_progress', prep_folder_path=NULL, "
        "gdrive_folder_url=NULL, apply_flag=1, stage_updated=?, updated_at=? "
        "WHERE id=?",
        (now, now, job["id"]),
    )
    db.commit()
    write_audit(db, job["id"], "stage", job["stage"], "prep_in_progress")
    log_event(
        "web_regen_dispatched",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
    )

    _launch_prep_subprocess(job)

    updated = _fetch_dashboard_row(db, fingerprint)
    assert updated is not None
    return _render_dashboard_row(request, updated, db)


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


@router.post("/board/jobs/{fingerprint}/waitlist", response_class=HTMLResponse)
def waitlist(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Defer a job to the Waitlist tab. Returns empty — row leaves the source tab."""
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] == "waitlisted":
        return HTMLResponse("")
    handle_waitlist(db, job)
    return HTMLResponse("")


@router.post("/board/jobs/{fingerprint}/reactivate", response_class=HTMLResponse)
def reactivate(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Restore a waitlisted job to scored or materials_drafted."""
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "waitlisted":
        raise HTTPException(status_code=409, detail="Job is not waitlisted")
    handle_reactivate(db, job)
    return HTMLResponse("")


_PROMOTABLE_STAGES = ("manual_review", "scored")


@router.post("/board/jobs/{fingerprint}/promote", response_class=HTMLResponse)
def promote(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Promote a job onto the Dashboard with relevance_score=7.

    Two surfaces invoke this:
    - Review tab: rows at stage='manual_review' (raises score, keeps stage)
    - Archive tab: rows at stage='scored' with score<7 (bumps score to 7
      so the row appears on the Dashboard's score>=7 filter).
    """
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] not in _PROMOTABLE_STAGES:
        raise HTTPException(status_code=409, detail="Job is not promotable from its current stage")
    promote_to_scored(db, job, reason="Promoted from web UI")
    return HTMLResponse("")


_POST_APPLICATION_STAGES = ("applied", "interview", "offer")


def _fetch_rejection_job(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    """handle_rejection needs relevance_score + prep_folder_path from the row."""
    return db.execute(
        "SELECT id, fingerprint, title, company, url, stage, relevance_score, prep_folder_path "
        "FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()


@router.post("/board/jobs/{fingerprint}/reject", response_class=HTMLResponse)
def reject(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    reason: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Reject a job. Writes feedback_log, moves prep folder to _rejected/, fires
    notify_waitlist_resurface. Returns empty — row drops off its source tab."""
    job = _fetch_rejection_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] == "rejected":
        return HTMLResponse("")
    handle_rejection(db, job, (reason or "").strip() or "Other")
    notify_waitlist_resurface(db, job["company"])
    return HTMLResponse("")


@router.post("/board/jobs/{fingerprint}/not-selected", response_class=HTMLResponse)
def not_selected(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    reason: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Mark that the company rejected the application. Drops a marker file in
    the existing _applied/ folder. Does NOT write feedback_log — company
    rejections must not contaminate the scorer. Fires notify_waitlist_resurface."""
    job = _fetch_rejection_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] == "not_selected":
        return HTMLResponse("")
    if job["stage"] not in _POST_APPLICATION_STAGES:
        raise HTTPException(
            status_code=409,
            detail="Not Selected only valid for applied/interview/offer stages",
        )
    handle_not_selected(db, job, (reason or "").strip() or "Company passed")
    notify_waitlist_resurface(db, job["company"])
    return HTMLResponse("")


@router.post("/board/jobs/{fingerprint}/notes", response_class=HTMLResponse)
def notes(
    fingerprint: str,
    request: Request,
    notes: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Write free-text user notes for the Applied tab. No audit log entry —
    notes are rewritten on every keystroke-debounce; audit is noise."""
    row = db.execute(
        "SELECT fingerprint, user_notes FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    db.execute(
        "UPDATE jobs SET user_notes=?, updated_at=datetime('now') WHERE fingerprint=?",
        (notes, fingerprint),
    )
    db.commit()
    updated = db.execute(
        "SELECT fingerprint, user_notes FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    assert updated is not None
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_notes_cell.html",
        context={"row": updated},
    )
