"""Manual JD ingest form — the web write surface that retires the Google
Form polling loop (#62).

``GET /ingest/`` renders the form; ``POST /ingest/manual`` writes straight
into ``jobs`` via :func:`findajob.ingest.ingest_manual_job` and returns an
HTMX partial for the result panel.

The form mode toggle at the top of the page scaffolds #131's Speculative
flow: the "Real posting" tab is active, the "Speculative" tab is disabled
with a link to #131 so the eventual POST sibling drops in without a
template rewrite.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from findajob.ingest import IngestResult, InvalidJobUrl, ingest_manual_job
from findajob.web.routes.materials import get_db

router = APIRouter()


@router.get("/ingest/", response_class=HTMLResponse)
def ingest_page(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Render the manual-JD form."""
    templates = request.app.state.templates
    try:
        today_speculative_count = db.execute(
            "SELECT COUNT(*) FROM speculative_requests WHERE date(submitted_at)=date('now', 'localtime')"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        # Table absent in legacy/test fixtures that haven't run init_db.py.
        # Falls back to no soft-warn — pipeline-correct default.
        today_speculative_count = 0
    return templates.TemplateResponse(
        request=request,
        name="ingest/form.html",
        context={"today_speculative_count": today_speculative_count},
    )


@router.post("/ingest/manual", response_class=HTMLResponse)
def submit_manual(
    request: Request,
    company: str = Form(...),
    title: str = Form(...),
    url: str = Form(...),
    raw_jd_text: str = Form(...),
    location: str = Form(""),
    remote_status: str = Form("Unknown"),
    notes: str = Form(""),
    known_contacts: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Ingest a manually-pasted JD. Returns an HTMX result partial."""
    missing = [
        name
        for name, value in (
            ("company", company),
            ("title", title),
            ("url", url),
            ("full JD text", raw_jd_text),
        )
        if not value.strip()
    ]
    if missing:
        return _render_result(
            request,
            outcome="error",
            message=f"Missing required field(s): {', '.join(missing)}.",
        )

    try:
        result: IngestResult = ingest_manual_job(
            db,
            company=company,
            title=title,
            url=url,
            location=location,
            remote_status=remote_status,
            notes=notes,
            known_contacts=known_contacts,
            raw_jd_text=raw_jd_text,
            source="web_manual",
        )
    except InvalidJobUrl as exc:
        return _render_result(request, outcome="error", message=str(exc))

    if result.status == "already_applied":
        return _render_result(
            request,
            outcome="already_applied",
            message=f"Already applied — {result.company} / {result.title}.",
            result=result,
        )

    if result.status == "not_selected":
        return _render_result(
            request,
            outcome="not_selected",
            message=(f"You were not selected for {result.company} / {result.title}. Here's where you left it:"),
            result=result,
        )

    if result.status == "resurfaced":
        stage_label = result.existing_stage or "unknown"
        return _render_result(
            request,
            outcome="resurfaced",
            message=(f"Re-surfaced to Dashboard — {result.company} / {result.title} (was {stage_label})."),
            result=result,
        )

    if result.status == "duplicate":
        return _render_result(
            request,
            outcome="duplicate",
            message=(
                f"Already in DB: {result.company} / {result.title} "
                f"(matched by {result.existing_match}). No new row created."
            ),
            result=result,
        )

    return _render_result(
        request,
        outcome="success",
        message=f"Ingested: {result.company} / {result.title}.",
        result=result,
    )


def _render_result(
    request: Request,
    *,
    outcome: str,
    message: str,
    result: IngestResult | None = None,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="ingest/_result.html",
        context={"outcome": outcome, "message": message, "result": result},
    )
