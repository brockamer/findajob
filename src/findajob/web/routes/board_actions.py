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
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.actions import (
    handle_not_selected,
    handle_reactivate,
    handle_rejection,
    handle_waitlist,
    notify_waitlist_resurface,
    promote_to_scored,
    snapshot_applied_md_files,
    un_apply_job,
    un_not_selected_job,
    un_reject_job,
    un_withdraw_job,
)
from findajob.audit import log_event, write_audit
from findajob.background_tasks import TASK_ID_ENV_VAR, record_failed, record_start
from findajob.classification import is_synthetic_job
from findajob.paths import BASE, IMAGE_ROOT
from findajob.spend_ceiling import check_launch_gate
from findajob.web.company_history import build_history_by_fp, fetch_company_history
from findajob.web.cron_dispatch import dispatch_cron
from findajob.web.filters import registry as filter_registry
from findajob.web.routes.materials import get_db

router = APIRouter()

MAX_CONCURRENT_PREPS = 3
"""Upper bound on simultaneously-running prep subprocesses.

Keeps LLM-API spending bounded when the operator mass-flags a morning's worth
of jobs. When the cap is reached, /prep and /regenerate return 429; the
dashboard row stays actionable so the operator can retry in a few minutes.
"""


def _prep_in_flight(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM jobs WHERE stage='prep_in_progress'").fetchone()[0]


def _prep_queue_full_response() -> HTMLResponse:
    return HTMLResponse(
        f"Prep queue full ({MAX_CONCURRENT_PREPS} in flight). Try again in a few minutes.",
        status_code=429,
    )


def _launch_prep_subprocess(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    *,
    kind: str = "prep",
    extra_args: tuple[str, ...] = (),
) -> int:
    """Insert a ``background_tasks`` row, then spawn prep_application.

    Returns the new task_id so a caller (e.g. status page) can poll.
    The subprocess reads ``FINDAJOB_BG_TASK_ID`` from env and writes
    back ``status='succeeded'``/``'failed'`` on exit. Watchdog reaps
    stuck rows after the kind-specific timeout in
    :data:`KIND_TIMEOUT_MINUTES`.

    ``kind`` and ``extra_args`` are how the Phase B route (#691)
    re-uses this launcher: ``kind='prep_phase_b'`` gives the watchdog
    a distinct row class to reap into ``briefing_ready`` (not
    ``scored``), and ``extra_args=('--phase=b',)`` tells the
    orchestrator to skip Phase A re-runs.
    """
    task_id = record_start(db, job_id=job["id"], kind=kind)
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                f"{IMAGE_ROOT}/scripts/prep_application.py",
                job["company"],
                job["title"],
                job["url"],
                job["id"],
                *extra_args,
            ],
            start_new_session=True,
            env={**os.environ, TASK_ID_ENV_VAR: str(task_id)},
        )
        # Backfill the PID once we have it. Best-effort; pid is
        # forensic-only and a missing one doesn't break the contract.
        db.execute("UPDATE background_tasks SET pid=? WHERE id=?", (proc.pid, task_id))
        db.commit()
    except Exception as e:
        record_failed(db, task_id, error_message=f"Popen failed: {e}")
        raise
    return task_id


def _launch_interview_prep_subprocess(db: sqlite3.Connection, job: sqlite3.Row) -> int:
    """Spawn the interview_prep generator. The orchestrator's own
    in-folder concurrency guard handles re-clicks; the
    ``background_tasks`` row provides the operator-visible status surface
    that the prior sentinel-file approach lacked."""
    task_id = record_start(db, job_id=job["id"], kind="interview_prep")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                f"{IMAGE_ROOT}/scripts/interview_prep.py",
                job["company"],
                job["title"],
                job["id"],
            ],
            start_new_session=True,
            env={**os.environ, TASK_ID_ENV_VAR: str(task_id)},
        )
        db.execute("UPDATE background_tasks SET pid=? WHERE id=?", (proc.pid, task_id))
        db.commit()
    except Exception as e:
        record_failed(db, task_id, error_message=f"Popen failed: {e}")
        raise
    return task_id


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
           CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied,
           (SELECT SUM(cl.cost_usd) FROM cost_log cl
            WHERE cl.job_id = j.id AND cl.cost_usd IS NOT NULL) AS cost
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


_STAGE_LABELS = {
    "applied": "Applied",
    "interview": "Interviewing",
    "offer": "Offer",
    "withdrawn": "Withdrawn",
    "not_selected": "Not Selected",
}


def _stage_change_toast_html(request: Request, new_stage: str) -> str:
    """Render the stage-change toast partial (#830) as a string for OOB swap."""
    label = _STAGE_LABELS.get(new_stage, new_stage)
    templates = request.app.state.templates
    return templates.get_template("board/_stage_change_toast.html").render({"message": f"Stage changed to {label}."})


def _applied_row_with_stage_toast(request: Request, row: sqlite3.Row, new_stage: str) -> HTMLResponse:
    """Render the Applied-tab row plus an OOB stage-change toast (#830).

    Used by /interview and /offer where the row stays on the Applied tab
    after the transition — HTMX needs both the primary <tr> swap and the
    OOB toast in one response. HTMX strips OOB elements before applying
    the primary swap, so concatenation order doesn't matter.
    """
    row_html = bytes(_render_applied_row(request, row).body).decode()
    return HTMLResponse(row_html + _stage_change_toast_html(request, new_stage))


def _transition_stage(
    db: sqlite3.Connection,
    job: sqlite3.Row,
    new_stage: str,
    event_name: str,
    *,
    changed_by: str | None = None,
) -> None:
    """Apply a plain stage transition: UPDATE, audit, log. No folder work."""
    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE jobs SET stage=?, stage_updated=?, updated_at=? WHERE id=?",
        (new_stage, now, now, job["id"]),
    )
    db.commit()
    write_audit(db, job["id"], "stage", job["stage"], new_stage, changed_by=changed_by)
    log_event(
        event_name,
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        stage=new_stage,
    )


def _move_folder_to_applied(db: sqlite3.Connection, job: sqlite3.Row) -> bool:
    """Move a prep folder from companies/ to companies/_applied/ + snapshot *.md.

    Snapshots every ``*.md`` in the moved folder to ``{name}.applied-{date}.md``
    siblings (#210) so later in-browser edits don't overwrite the as-sent state.

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
    snapshot_applied_md_files(dest)
    log_event("folder_moved_to_applied", job_id=job["id"], folder=os.path.basename(folder))
    return True


def _fetch_job(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT id, fingerprint, title, company, url, stage, synthetic FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()


@router.post("/board/jobs/{fingerprint}/prep", response_class=HTMLResponse)
def prep(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    refusal = check_launch_gate(db)
    if refusal is not None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Monthly LLM spend ceiling reached: ${refusal.current_sum_usd:.2f} / "
                f"${refusal.ceiling_usd:.2f}. Raise or disable the ceiling in /settings/."
            ),
        )

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

    # Phase A only — operator continues from the briefing-first gate at
    # /materials/{fp}/ by POSTing to /continue-prep (or rejects with a
    # substantive reason). Spec: docs/superpowers/specs/2026-05-16-622-prep-cost-gate-design.md.
    _launch_prep_subprocess(db, job, extra_args=("--phase=a",))

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
    refusal = check_launch_gate(db)
    if refusal is not None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Monthly LLM spend ceiling reached: ${refusal.current_sum_usd:.2f} / "
                f"${refusal.ceiling_usd:.2f}. Raise or disable the ceiling in /settings/."
            ),
        )

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

    _execute_regenerate(db, job, source_event="web_regen_dispatched")

    updated = _fetch_dashboard_row(db, fingerprint)
    assert updated is not None
    return _render_dashboard_row(request, updated, db)


def _execute_regenerate(db: sqlite3.Connection, job: sqlite3.Row, *, source_event: str) -> None:
    """Side effects of regenerate after gates have passed.

    Caller must already have verified: job exists, ``stage != 'prep_in_progress'``,
    and prep queue is below ``MAX_CONCURRENT_PREPS``. Used by both the dashboard
    handler (returns HTMX row) and the materials-page handler (returns redirect)
    so the side-effect sequence stays in one place.
    """
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
        source_event,
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
    )

    _launch_prep_subprocess(db, job)


@router.get("/board/jobs/{fingerprint}/regenerate/confirm", response_class=HTMLResponse)
def regenerate_confirm(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Render the regenerate confirm modal into the row's status cell.

    Triggered by the Dashboard dropdown's Regenerate option (#700). Confirm
    posts to /regenerate; Cancel restores the status cell via /regenerate/cell.
    Returns 404 for unknown fingerprint, 409 for stages outside the dropdown's
    Regenerate-visible set (matches _status_cell.html's `{% if stage in
    ('prep_in_progress', 'materials_drafted') %}` gate).
    """
    row = db.execute(
        "SELECT id, fingerprint, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["stage"] not in ("materials_drafted", "prep_in_progress"):
        raise HTTPException(
            status_code=409,
            detail="Regenerate is only valid for prep_in_progress or materials_drafted",
        )

    # Pin on old_value='prep_in_progress' — handle_reactivate also writes a
    # ('waitlisted' → 'materials_drafted') audit row; without the old_value
    # filter, a reactivation timestamp would surface as "Last generated".
    last_prep_utc = db.execute(
        "SELECT MAX(changed_at) FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' "
        "AND old_value='prep_in_progress' AND new_value='materials_drafted'",
        (row["id"],),
    ).fetchone()[0]

    context_lines: list[str] = []
    if last_prep_utc:
        pt = ZoneInfo("America/Los_Angeles")
        utc = ZoneInfo("UTC")
        dt = datetime.fromisoformat(last_prep_utc).replace(tzinfo=utc).astimezone(pt)
        context_lines.append(f"Last generated: {dt.strftime('%Y-%m-%d %H:%M %Z')}")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_confirm_modal.html",
        context={
            "copy": (
                "Regenerating will delete your tailored materials for this job "
                "(resume, cover letter, recruiter critique, outreach drafts). Continue?"
            ),
            "context_lines": context_lines,
            "confirm_url": f"/board/jobs/{fingerprint}/regenerate",
            "confirm_target": "closest tr",
            "cancel_url": f"/board/jobs/{fingerprint}/regenerate/cell",
        },
    )


@router.get("/board/jobs/{fingerprint}/regenerate/cell", response_class=HTMLResponse)
def regenerate_cell(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Re-render the Dashboard status cell — Cancel-restoration endpoint for
    the regenerate confirm modal (#700). Returns 404 for unknown fingerprint."""
    row = _fetch_dashboard_row(db, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_status_cell.html",
        context={"row": row, "tab": "dashboard"},
    )


@router.post("/board/jobs/{fingerprint}/continue-prep", response_class=HTMLResponse)
def continue_prep(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Promote a briefing_ready job into the Phase B subprocess (#691).

    The briefing-first gate's "continue" half — the operator has read
    the Phase A briefing on ``/materials/{fp}/`` and decided to spend
    the Phase B budget (~$0.63–1.80 for tailor → cover → critique →
    outreach). Gates mirror ``/prep`` exactly: 404 → idempotent on
    already-advanced → 409 → 402 (spend ceiling) → 429 (queue cap).

    Stage transition (``briefing_ready → prep_in_progress``) happens
    BEFORE the subprocess spawn so the watchdog + concurrency cap
    observe a consistent in-flight state — same invariant as
    ``/prep``. Failure paths route to ``_handle_phase_b_failure``
    (inside the orchestrator) which resets to ``briefing_ready`` and
    preserves the prep folder so the operator can retry without
    re-paying Phase A.
    """
    refusal = check_launch_gate(db)
    if refusal is not None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Monthly LLM spend ceiling reached: ${refusal.current_sum_usd:.2f} / "
                f"${refusal.ceiling_usd:.2f}. Raise or disable the ceiling in /settings/."
            ),
        )

    row = _fetch_dashboard_row(db, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Idempotency: already in flight or already done — return current row.
    if row["stage"] in ("prep_in_progress", "materials_drafted"):
        return _render_dashboard_row(request, row, db)

    if row["stage"] != "briefing_ready":
        raise HTTPException(
            status_code=409,
            detail="Continue-prep only valid for jobs at stage='briefing_ready'",
        )

    if _prep_in_flight(db) >= MAX_CONCURRENT_PREPS:
        return _prep_queue_full_response()

    job = db.execute(
        "SELECT id, title, company, url, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()

    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE jobs SET stage='prep_in_progress', stage_updated=?, updated_at=? WHERE id=?",
        (now, now, job["id"]),
    )
    db.commit()
    write_audit(db, job["id"], "stage", job["stage"], "prep_in_progress")
    log_event(
        "web_continue_prep_dispatched",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
    )

    _launch_prep_subprocess(db, job, kind="prep_phase_b", extra_args=("--phase=b",))

    updated = _fetch_dashboard_row(db, fingerprint)
    assert updated is not None
    return _render_dashboard_row(request, updated, db)


@router.post("/board/jobs/{fingerprint}/apply", response_class=HTMLResponse)
def apply(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Move job to the Applied tab. Response body is the undo toast partial
    (#699 F3) carrying an out-of-band swap into #undo-toast — HTMX strips the
    OOB element from the response before doing the primary swap into the row,
    so the row is removed *and* the toast appears in the same request.

    Idempotency: re-clicking on an already-applied row returns empty (no toast)
    — the 30s window has either already passed or is being handled by an
    earlier in-flight toast."""
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] == "applied":
        return HTMLResponse("")
    changed_by = "outreach_button" if is_synthetic_job(job) else "user"
    _transition_stage(db, job, "applied", event_name="web_applied", changed_by=changed_by)
    _move_folder_to_applied(db, job)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_undo_toast.html",
        context={"fingerprint": fingerprint},
    )


@router.post("/board/jobs/{fingerprint}/un-apply", response_class=HTMLResponse)
def un_apply(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Reverse a recent /apply within the 30-second undo window (#699 F3).

    Gates:
      - 404 if fingerprint unknown
      - 409 if stage != 'applied' (the dropdown surface is no longer applicable)
      - 409 if no audit_log row '… → applied' within the last 30 seconds
        (the undo window has expired)

    On success, returns empty body — HTMX's outerHTML swap of the toast cell
    with empty content removes it from the DOM. The row reappears on the
    Dashboard at the next page load (not auto-inserted)."""
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "applied":
        raise HTTPException(status_code=409, detail="Job is not at stage='applied'")

    # SQL-side window check — both /apply's write_audit and this gate use
    # SQLite's datetime('now'), so test seeds and production writes are
    # comparable without Python/DB clock drift.
    recent = db.execute(
        "SELECT 1 FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='applied' "
        "  AND changed_at > datetime('now', '-30 seconds') "
        "LIMIT 1",
        (job["id"],),
    ).fetchone()
    if recent is None:
        raise HTTPException(status_code=409, detail="Undo window expired")

    un_apply_job(db, job)
    return HTMLResponse("")


@router.post("/board/jobs/{fingerprint}/interview", response_class=HTMLResponse)
def interview(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    refusal = check_launch_gate(db)
    if refusal is not None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Monthly LLM spend ceiling reached: ${refusal.current_sum_usd:.2f} / "
                f"${refusal.ceiling_usd:.2f}. Raise or disable the ceiling in /settings/."
            ),
        )

    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "interview":
        _transition_stage(db, job, "interview", event_name="web_interview")
    # Re-clicking "Interviewing" regenerates the interview-prep artifact.
    # Concurrency control via background_tasks (M6); the sentinel-file
    # approach was removed when M6's row-based status surface landed.
    _launch_interview_prep_subprocess(db, job)
    updated = _fetch_applied_row(db, fingerprint)
    assert updated is not None
    return _applied_row_with_stage_toast(request, updated, "interview")


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
    return _applied_row_with_stage_toast(request, updated, "offer")


@router.post("/board/jobs/{fingerprint}/withdraw", response_class=HTMLResponse)
def withdraw(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Withdraw from the application. Row drops off Applied; OOB stage-change
    toast confirms the transition (#830)."""
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] == "withdrawn":
        return HTMLResponse("")
    _transition_stage(db, job, "withdrawn", event_name="web_withdrawn")
    notify_waitlist_resurface(db, job["company"])
    return HTMLResponse(_stage_change_toast_html(request, "withdrawn"))


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


@router.post("/board/jobs/{fingerprint}/reactivate-and-prep", response_class=HTMLResponse)
def reactivate_and_prep(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Collapse the two-step waitlist→reactivate→prep flow (#702 G9).

    Order of gates: 404 → idempotent-success-on-already-advanced → 409 →
    402 (spend ceiling) → 429 (queue cap). Mutations only run after all
    gates pass, so a tripped gate leaves the row at stage='waitlisted'.

    Writes two audit rows for traceability — handle_reactivate writes
    (waitlisted → scored | materials_drafted), then this route writes
    (* → prep_in_progress).
    """
    job = _fetch_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Idempotency: match /prep's silent-success on already-in-flight rows so
    # a fast double-click doesn't surface a 409.
    if job["stage"] in ("prep_in_progress", "materials_drafted"):
        return HTMLResponse("")

    if job["stage"] != "waitlisted":
        raise HTTPException(status_code=409, detail="Job is not waitlisted")

    refusal = check_launch_gate(db)
    if refusal is not None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Monthly LLM spend ceiling reached: ${refusal.current_sum_usd:.2f} / "
                f"${refusal.ceiling_usd:.2f}. Raise or disable the ceiling in /settings/."
            ),
        )

    if _prep_in_flight(db) >= MAX_CONCURRENT_PREPS:
        return _prep_queue_full_response()

    handle_reactivate(db, job)

    # Re-read intermediate stage so the audit row's old_value matches whatever
    # handle_reactivate landed on (scored if no folder, materials_drafted if folder).
    intermediate_stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]

    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE jobs SET stage='prep_in_progress', apply_flag=1, stage_updated=?, updated_at=? WHERE id=?",
        (now, now, job["id"]),
    )
    db.commit()
    write_audit(db, job["id"], "stage", intermediate_stage, "prep_in_progress")
    log_event(
        "web_reactivate_and_prep_dispatched",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
    )

    # _launch_prep_subprocess wants the full job row with url
    full_job = db.execute("SELECT id, title, company, url, stage FROM jobs WHERE id=?", (job["id"],)).fetchone()
    _launch_prep_subprocess(db, full_job)

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


def _fetch_un_reject_job(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    """un_reject_job reads prep_folder_path and reject_reason off the row."""
    return db.execute(
        "SELECT id, fingerprint, title, company, url, stage, prep_folder_path, reject_reason "
        "FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()


@router.post("/board/jobs/{fingerprint}/un-reject", response_class=HTMLResponse)
def un_reject(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Reverse a user rejection of a job — clears the feedback_log row,
    restores stage='scored', moves the prep folder out of _rejected/, sets
    relevance_score=8. Only valid for stage='rejected' (user rejection);
    rows at stage='not_selected' (company rejection) cannot be revived
    this way and return 409.
    """
    job = _fetch_un_reject_job(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "rejected":
        raise HTTPException(status_code=409, detail="Only user-rejected jobs can be un-rejected")
    un_reject_job(db, job, overwrite_fields={})
    return HTMLResponse("")


def _fetch_un_reject_job_with_date(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    """Like _fetch_un_reject_job but JOINs audit_log for the rejection date.

    Mirrors the LEFT JOIN subquery from _rejected_source() in board.py so the
    confirm-modal context can show 'rejected on YYYY-MM-DD'. Indexed by j.id;
    the GROUP BY + MAX(changed_at) handles re-reject sequences.
    """
    return db.execute(
        "SELECT j.id, j.fingerprint, j.title, j.company, j.url, j.stage, "
        "       j.reject_reason, j.synthetic, al.rejected_date "
        "FROM jobs j "
        "LEFT JOIN ( "
        "  SELECT job_id, MAX(changed_at) AS rejected_date "
        "  FROM audit_log "
        "  WHERE field_changed='stage' AND new_value IN ('rejected','not_selected') "
        "  GROUP BY job_id "
        ") al ON al.job_id = j.id "
        "WHERE j.fingerprint=?",
        (fingerprint,),
    ).fetchone()


@router.get("/board/jobs/{fingerprint}/un-reject/confirm", response_class=HTMLResponse)
def un_reject_confirm(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Render the un-reject confirm modal into the row's un-reject cell.

    Only valid for stage='rejected'. Returns 404 on unknown fingerprint and
    409 on any other stage (not_selected, scored, applied, etc.).
    """
    job = _fetch_un_reject_job_with_date(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "rejected":
        raise HTTPException(status_code=409, detail="Only user-rejected jobs can be un-rejected")

    context_lines = []
    if job["rejected_date"]:
        context_lines.append(f"Rejected: {job['rejected_date']}")
    if job["reject_reason"]:
        context_lines.append(f"Reason: {job['reject_reason']}")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_confirm_modal.html",
        context={
            "copy": "This deletes the scorer's feedback signal for this rejection. Continue?",
            "context_lines": context_lines,
            "confirm_url": f"/board/jobs/{fingerprint}/un-reject",
            "confirm_target": "closest tr",
            "cancel_url": f"/board/jobs/{fingerprint}/un-reject/cell",
        },
    )


@router.get("/board/jobs/{fingerprint}/un-reject/cell", response_class=HTMLResponse)
def un_reject_cell(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Render the un-reject button cell — the Cancel-restoration endpoint
    for the confirm modal swap. Returns 404 on unknown fingerprint; cells
    on non-rejected rows render the inert dash (no 409)."""
    row = db.execute(
        "SELECT fingerprint, stage, title FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_unreject_cell.html",
        context={"row": row},
    )


@router.post("/board/jobs/{fingerprint}/change-reject-reason", response_class=HTMLResponse)
def change_reject_reason(
    fingerprint: str,
    request: Request,
    reason: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Update jobs.reject_reason in place. No folder move, no feedback_log touch.
    Writes audit_log so the change is durable in history.

    Matches /reject's no-validation convention — any non-empty string is
    accepted; blank defaults to 'Other'. Returns the re-rendered cell.
    """
    row = db.execute(
        "SELECT id, fingerprint, stage, reject_reason FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["stage"] != "rejected":
        raise HTTPException(status_code=409, detail="Only user-rejected jobs can have their reason changed")

    new_reason = (reason or "").strip() or "Other"
    old_reason = row["reject_reason"] or ""

    if new_reason != old_reason:
        db.execute(
            "UPDATE jobs SET reject_reason=?, updated_at=datetime('now') WHERE id=?",
            (new_reason, row["id"]),
        )
        write_audit(db, row["id"], "reject_reason", old_reason, new_reason, changed_by="user")
        db.commit()

    updated = db.execute(
        "SELECT fingerprint, stage, reject_reason FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_change_reject_reason_cell.html",
        context={"row": updated},
    )


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
    request: Request,
    reason: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Mark that the company rejected the application. Drops a marker file in
    the existing _applied/ folder. Does NOT write feedback_log — company
    rejections must not contaminate the scorer. Fires notify_waitlist_resurface.
    Row drops off the source tab; OOB stage-change toast confirms (#830)."""
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
    log_event(
        "board_not_selected",
        fingerprint=fingerprint,
        reason=(reason or "").strip() or "Company passed",
        prior_stage=job["stage"],
    )
    return HTMLResponse(_stage_change_toast_html(request, "not_selected"))


def _fetch_not_selected_row(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    """Row shape needed by un_not_selected_job — id, stage, folder, reason."""
    return db.execute(
        "SELECT id, fingerprint, title, company, url, stage, prep_folder_path, reject_reason "
        "FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()


@router.post("/board/jobs/{fingerprint}/un-not-selected", response_class=HTMLResponse)
def un_not_selected(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Reverse a company-not-selected stage. Restores the prior stage from
    audit_log (fallback 'applied'), deletes NOT_SELECTED_*.txt markers from
    the job's _applied/ folder. Row drops off Not Selected tab; OOB
    stage-change toast names the restored stage (#830).
    """
    job = _fetch_not_selected_row(db, fingerprint)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["stage"] != "not_selected":
        raise HTTPException(status_code=409, detail="Only not_selected jobs can be un-not-selected")
    restored_stage = un_not_selected_job(db, job)
    return HTMLResponse(_stage_change_toast_html(request, restored_stage))


@router.post("/board/jobs/{fingerprint}/change-not-selected-reason", response_class=HTMLResponse)
def change_not_selected_reason(
    fingerprint: str,
    request: Request,
    reason: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Update jobs.reject_reason for a not_selected row. Mirrors
    change-reject-reason from #697: no validation, blank defaults to 'Other',
    writes audit_log with changed_by='user'."""
    row = db.execute(
        "SELECT id, fingerprint, stage, reject_reason FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["stage"] != "not_selected":
        raise HTTPException(
            status_code=409, detail="Only not_selected jobs can have their reason changed via this route"
        )

    new_reason = (reason or "").strip() or "Other"
    old_reason = row["reject_reason"] or ""

    if new_reason != old_reason:
        db.execute(
            "UPDATE jobs SET reject_reason=?, updated_at=datetime('now') WHERE id=?",
            (new_reason, row["id"]),
        )
        write_audit(db, row["id"], "reject_reason", old_reason, new_reason, changed_by="user")
        db.commit()

    updated = db.execute(
        "SELECT fingerprint, stage, reject_reason FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_change_not_selected_reason_cell.html",
        context={"row": updated},
    )


@router.post("/board/jobs/{fingerprint}/notes", response_class=HTMLResponse)
def notes(
    fingerprint: str,
    request: Request,
    notes: str = Form(""),
    event_type: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Write free-text user notes. Tab-agnostic — fires from any board tab
    where the user_notes column is visible.

    The handler always overwrites jobs.user_notes (live experience preserved
    on both blur and keyup-debounce triggers). A row is appended to
    notes_history ONLY when event_type == 'blur' — keyup writes would flood
    the table with mid-edit keystrokes. The event_type form param is
    injected client-side by hx-on::config-request reading event.type from
    the DOM event; HTMX request headers don't carry event type.
    """
    row = db.execute(
        "SELECT id, fingerprint, user_notes FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    db.execute(
        "UPDATE jobs SET user_notes=?, updated_at=datetime('now') WHERE fingerprint=?",
        (notes, fingerprint),
    )
    if event_type == "blur":
        db.execute(
            "INSERT INTO notes_history (job_id, notes) VALUES (?, ?)",
            (row["id"], notes),
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


@router.get("/board/jobs/{fingerprint}/notes/history", response_class=HTMLResponse)
def notes_history(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Render the notes_history disclosure fragment for one job.

    Lazy-loaded on <details> first-open via hx-trigger="toggle once". PT
    rendering happens server-side; the template stays Jinja-pure.
    """
    job = db.execute("SELECT id FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    raw_rows = db.execute(
        "SELECT notes, updated_at FROM notes_history WHERE job_id=? ORDER BY updated_at DESC, id DESC",
        (job["id"],),
    ).fetchall()

    pt = ZoneInfo("America/Los_Angeles")
    utc = ZoneInfo("UTC")
    rows = []
    for r in raw_rows:
        # updated_at is stored as 'YYYY-MM-DD HH:MM:SS' (naive UTC, sqlite datetime())
        dt = datetime.fromisoformat(r["updated_at"]).replace(tzinfo=utc).astimezone(pt)
        rows.append(
            {
                "notes": r["notes"],
                "updated_at_pt": dt.strftime("%Y-%m-%d %H:%M %Z"),
            }
        )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_notes_history.html",
        context={"rows": rows},
    )


# ── Archive actions (#701) ─────────────────────────────────────────────────


@router.post("/board/jobs/{fingerprint}/un-withdraw", response_class=HTMLResponse)
def un_withdraw(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Reverse a withdraw stage transition. Restores prior stage from
    audit_log (fallback 'applied'). Row vanishes from Archive's
    withdraw-filter view; OOB stage-change toast names the restored
    stage (#830)."""
    row = db.execute(
        "SELECT id, fingerprint, title, company, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["stage"] != "withdrawn":
        raise HTTPException(status_code=409, detail="Only withdrawn jobs can be un-withdrawn")
    restored_stage = un_withdraw_job(db, row)
    return HTMLResponse(_stage_change_toast_html(request, restored_stage))


@router.get("/board/jobs/{fingerprint}/reattribute/modal", response_class=HTMLResponse)
def reattribute_modal(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Render the reattribute modal partial for a not_selected Archive row.

    404 unknown, 409 if stage != 'not_selected' (reattribute only makes
    sense for company-rejected rows that may have been mis-attributed).
    """
    row = db.execute(
        "SELECT fingerprint, title, company, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["stage"] != "not_selected":
        raise HTTPException(status_code=409, detail="Reattribute is only valid for not_selected jobs")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_reattribute_modal.html",
        context={"row": row},
    )


@router.get("/board/jobs/{fingerprint}/archive-actions-cell", response_class=HTMLResponse)
def archive_actions_cell(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Cancel-restore endpoint for the reattribute modal. Renders the
    original 4-stage actions cell for the row.
    """
    row = db.execute(
        "SELECT fingerprint, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_archive_actions_cell.html",
        context={"row": row},
    )


@router.get("/board/jobs/search", response_class=HTMLResponse)
def jobs_search(
    request: Request,
    search: str = Query(default=""),
    exclude: str = Query(default=""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Autocomplete search for the reattribute modal. Returns up to 10
    title/company LIKE matches in stages where re-attribution makes sense
    (applied, interview, offer, withdrawn, rejected, not_selected),
    excluding the current row's fingerprint.

    Returns empty rows list when query is blank (short-circuit).
    """
    q = (search or "").strip()
    if not q:
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="board/_reattribute_search_results.html",
            context={"rows": [], "query": ""},
        )
    pattern = f"%{q}%"
    rows = db.execute(
        "SELECT fingerprint, title, company, stage FROM jobs "
        "WHERE (title LIKE ? OR company LIKE ?) "
        "  AND stage IN ('applied','interview','offer','withdrawn','rejected','not_selected') "
        "  AND fingerprint != ? "
        "ORDER BY stage_updated DESC, created_at DESC "
        "LIMIT 10",
        (pattern, pattern, exclude),
    ).fetchall()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_reattribute_search_results.html",
        context={"rows": rows, "query": q},
    )


@router.post("/board/jobs/{fingerprint}/reattribute-from-archive", response_class=HTMLResponse)
def reattribute_from_archive(
    fingerprint: str,
    request: Request,  # noqa: ARG001
    target_fingerprint: str = Form(""),
    reason: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Reattribute the rejection from the current not_selected row to a
    different job. Calls un_not_selected_job on current row (restores prior
    stage, queues marker-file deletions), then handle_not_selected on target
    with changed_by='archive_reattribute' (queues marker-file write).

    Both helpers share a ``deferred_fs`` list so source-restore + target-mark
    land atomically: DB writes accumulate, a single trailing ``db.commit()``
    persists them, then the queued filesystem ops execute in order. If
    either helper raises mid-call, neither DB writes nor filesystem changes
    have happened — the operator sees the error rather than a half-applied
    reattribution (#709, supersedes the ``commit=False`` shape from #707).

    Atomic: 404 on unknown source or target; 409 if source stage !=
    'not_selected' or if target_fingerprint missing/blank.
    """
    if not target_fingerprint:
        raise HTTPException(status_code=409, detail="target_fingerprint is required")

    source = db.execute(
        "SELECT id, fingerprint, title, company, url, stage, prep_folder_path, reject_reason "
        "FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if source is None:
        raise HTTPException(status_code=404, detail="Source job not found")
    if source["stage"] != "not_selected":
        raise HTTPException(status_code=409, detail="Reattribute source must be not_selected")

    target = db.execute(
        "SELECT id, fingerprint, title, company, url, stage, prep_folder_path FROM jobs WHERE fingerprint=?",
        (target_fingerprint,),
    ).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="Target job not found")

    final_reason = (reason or "").strip() or "Reattributed"

    deferred_fs: list[Callable[[], None]] = []
    try:
        un_not_selected_job(db, source, deferred_fs=deferred_fs)
        handle_not_selected(db, target, final_reason, changed_by="archive_reattribute", deferred_fs=deferred_fs)
        db.commit()
    except Exception:
        db.rollback()
        raise

    for fs_op in deferred_fs:
        fs_op()

    return HTMLResponse("")


@router.post("/board/trigger-triage")
def trigger_triage(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Manually fire ``scripts/triage.py`` from the dashboard first-triage
    banner (#752). Delegates to the shared ``dispatch_cron`` (#650) so the
    launch path stays single-sourced with ``/tools/trigger-cron/triage``.
    Override ``redirect_url`` to preserve the banner's destination.
    """
    base_root = request.app.state.base_root
    return dispatch_cron(
        "triage",
        db,
        base_root,
        source="dashboard_banner",
        redirect_url="/board/dashboard?triage_launched=1",
    )
