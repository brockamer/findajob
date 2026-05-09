"""Rejection-review surface for #362 (Task 5).

The detector script (``scripts/detect_rejections.py``, Task 4) writes pending
rows into ``rejection_suggestions`` whenever Gmail flags a likely company
rejection. This module renders the operator's review queue and exposes the
three confirm/dismiss/reattribute endpoints.

Per spec §4.5: never auto-flips; always operator-in-loop. Confirm calls
``handle_not_selected(..., changed_by='gmail_rejection_detector')`` so the
audit trail distinguishes Gmail-confirmed transitions from manual flips.

Routes:
    GET  /board/rejections-review/
        Render the review queue (pending rows, newest detected first).
    POST /board/rejections-review/{id}/confirm
        Apply the suggestion to the matched job.
    POST /board/rejections-review/{id}/dismiss
        Operator says "this isn't a rejection" — leave the job's stage
        alone, mark the suggestion ``user_action='dismissed'``.
    POST /board/rejections-review/{id}/reattribute
        Operator picks a different ``jobs.id`` (form field ``job_id``).
        Updates ``user_chose_job_id``, applies ``handle_not_selected``
        to that job, marks the suggestion ``user_action='reassigned'``.

HTMX support: confirm/dismiss/reattribute return an empty 200 on
HX-Request so the calling card swaps out (``hx-target`` removes itself);
non-HTMX POSTs redirect back to the index.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.actions import handle_not_selected
from findajob.audit import log_event
from findajob.web.routes.materials import get_db

router = APIRouter()

_AUDIT_TAG = "gmail_rejection_detector"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _load_suggestion(db: sqlite3.Connection, suggestion_id: int) -> sqlite3.Row:
    row = db.execute(
        """
        SELECT id, gmail_message_id, received_at, detected_at, sender, subject,
               body_excerpt, extracted_company, extracted_role, matched_job_id,
               match_status, confidence, suggested_reason, user_action,
               user_action_at, user_chose_job_id
        FROM rejection_suggestions
        WHERE id = ?
        """,
        (suggestion_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    return row


def _load_job(db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT id, title, company, stage, prep_folder_path FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return row


def _hx_or_redirect(request: Request, status: int = 200) -> HTMLResponse | RedirectResponse:
    """Return an HTMX-friendly empty fragment, or redirect back to the index."""
    if request.headers.get("HX-Request"):
        return HTMLResponse("", status_code=status)
    return RedirectResponse(url="/board/rejections-review/", status_code=303)


@router.get("/board/rejections-review/", response_class=HTMLResponse)
def index(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Pending rejection-suggestion cards, newest detected first."""
    rows = db.execute(
        """
        SELECT s.id, s.received_at, s.detected_at, s.sender, s.subject,
               s.body_excerpt, s.extracted_company, s.extracted_role,
               s.matched_job_id, s.match_status, s.confidence,
               s.suggested_reason,
               j.title AS matched_title, j.company AS matched_company,
               j.stage AS matched_stage
        FROM rejection_suggestions s
        LEFT JOIN jobs j ON j.id = s.matched_job_id
        WHERE s.user_action = 'pending'
        ORDER BY s.detected_at DESC, s.id DESC
        """
    ).fetchall()

    items = [
        {
            "id": r["id"],
            "received_at": r["received_at"],
            "detected_at": r["detected_at"],
            "sender": r["sender"],
            "sender_domain": r["sender"].split("@", 1)[-1] if r["sender"] and "@" in r["sender"] else r["sender"],
            "subject": r["subject"],
            "body_excerpt": r["body_excerpt"],
            "extracted_company": r["extracted_company"],
            "extracted_role": r["extracted_role"],
            "matched_job_id": r["matched_job_id"],
            "match_status": r["match_status"],
            "confidence": r["confidence"],
            "suggested_reason": r["suggested_reason"],
            "matched_title": r["matched_title"],
            "matched_company": r["matched_company"],
            "matched_stage": r["matched_stage"],
        }
        for r in rows
    ]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="rejections_review.html",
        context={"items": items, "pending_count": len(items)},
    )


@router.post("/board/rejections-review/{suggestion_id}/confirm", response_model=None)
def confirm(
    suggestion_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Apply ``not_selected`` to the matched job and mark the suggestion confirmed."""
    suggestion = _load_suggestion(db, suggestion_id)

    if suggestion["user_action"] != "pending":
        # Idempotent — operator double-clicked or HTMX retried.
        return _hx_or_redirect(request)

    job_id = suggestion["matched_job_id"]
    if not job_id:
        raise HTTPException(
            status_code=409,
            detail="No matched job — use Reattribute to pick one before confirming.",
        )
    job = _load_job(db, job_id)

    if job["stage"] not in ("applied", "interview", "offer"):
        raise HTTPException(
            status_code=409,
            detail=f"Job stage is '{job['stage']}'; only applied/interview/offer can be marked not_selected.",
        )

    handle_not_selected(db, job, suggestion["suggested_reason"], changed_by=_AUDIT_TAG)

    db.execute(
        "UPDATE rejection_suggestions SET user_action = 'confirmed', user_action_at = ? WHERE id = ?",
        (_now_iso(), suggestion_id),
    )
    db.commit()

    log_event(
        "rejection_suggestion_confirmed",
        suggestion_id=suggestion_id,
        job_id=job_id,
        reason=suggestion["suggested_reason"],
        confidence=suggestion["confidence"],
    )

    return _hx_or_redirect(request)


@router.post("/board/rejections-review/{suggestion_id}/dismiss", response_model=None)
def dismiss(
    suggestion_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Operator-rejected the suggestion. Leave the job's stage; mark the row dismissed."""
    suggestion = _load_suggestion(db, suggestion_id)

    if suggestion["user_action"] != "pending":
        return _hx_or_redirect(request)

    db.execute(
        "UPDATE rejection_suggestions SET user_action = 'dismissed', user_action_at = ? WHERE id = ?",
        (_now_iso(), suggestion_id),
    )
    db.commit()

    log_event("rejection_suggestion_dismissed", suggestion_id=suggestion_id)

    return _hx_or_redirect(request)


@router.post("/board/rejections-review/{suggestion_id}/reattribute", response_model=None)
def reattribute(
    suggestion_id: int,
    request: Request,
    job_id: str = Form(...),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Apply ``not_selected`` to a job the operator picked; mark the suggestion reassigned."""
    suggestion = _load_suggestion(db, suggestion_id)

    if suggestion["user_action"] != "pending":
        return _hx_or_redirect(request)

    job = _load_job(db, job_id)

    if job["stage"] not in ("applied", "interview", "offer"):
        raise HTTPException(
            status_code=409,
            detail=f"Job stage is '{job['stage']}'; only applied/interview/offer can be marked not_selected.",
        )

    handle_not_selected(db, job, suggestion["suggested_reason"], changed_by=_AUDIT_TAG)

    db.execute(
        """
        UPDATE rejection_suggestions
        SET user_action = 'reassigned', user_action_at = ?, user_chose_job_id = ?
        WHERE id = ?
        """,
        (_now_iso(), job_id, suggestion_id),
    )
    db.commit()

    log_event(
        "rejection_suggestion_reassigned",
        suggestion_id=suggestion_id,
        original_matched_job_id=suggestion["matched_job_id"],
        operator_chose_job_id=job_id,
        reason=suggestion["suggested_reason"],
    )

    return _hx_or_redirect(request)


@router.get("/board/rejections-review/widget", response_class=HTMLResponse)
def widget(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Dashboard partial — renders an info bar when pending count > 0, empty otherwise."""
    n = db.execute("SELECT COUNT(*) FROM rejection_suggestions WHERE user_action = 'pending'").fetchone()[0]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_rejections_widget.html",
        context={"rejections_pending": n},
    )
