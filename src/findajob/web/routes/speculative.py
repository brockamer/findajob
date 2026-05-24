"""Web routes for speculative ingest (#131 B3).

Endpoints:
    POST /ingest/speculative              — form submit (kicks subprocess)
    GET  /speculative/status/{id}         — async status page (HTMX poll)
    GET  /speculative/status/{id}/poll    — HTMX poll fragment
    GET  /speculative/review/{id}         — review page (briefing + role cards)
    POST /speculative/approve/{id}        — write jobs rows from kept cards
    POST /speculative/regenerate/{id}     — re-run research (resets status to researching)
    POST /speculative/trash/{id}          — drop submission, no jobs rows written
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.background_tasks import TASK_ID_ENV_VAR, record_failed, record_start
from findajob.db import connect
from findajob.paths import BASE, IMAGE_ROOT
from findajob.speculative.approver import approve_request
from findajob.speculative.parser import parse_role_cards
from findajob.spend_ceiling import check_launch_gate
from findajob.web.markdown import render_markdown

router = APIRouter()
# #635: every other route module renders via ``request.app.state.templates``,
# which ``findajob.web.app.create_app`` populates with all Jinja globals
# (``onboarding_complete``, ``reject_reason_options``, …).
# Pre-#635 this module built its own ``Jinja2Templates(...)`` with no globals
# registered — production speculative status/review renders would have 500'd
# after #618 added ``{% if onboarding_complete(request) %}`` to ``_nav.html``,
# but speculative is cold-outreach so the path stayed untriggered until the
# #631 test sweep surfaced it.

DB_PATH = Path(BASE) / "data" / "pipeline.db"


def _conn() -> sqlite3.Connection:
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_bg_task(conn: sqlite3.Connection, request_id: int) -> dict | None:
    """Return the most recent ``background_tasks`` row for this speculative
    request, or None if no row exists.

    Latest by ``id`` (matches insert order). Speculative regenerate spawns
    a new subprocess (and inserts a new row) without finalizing the prior
    one when the prior is already terminal — see ``record_start`` in
    ``findajob.background_tasks``. The status page wants the most recent
    so the operator sees the current run's PID + timestamps.
    """
    row = conn.execute(
        "SELECT id, job_id, kind, started_at, finished_at, status, error_message, pid "
        "FROM background_tasks "
        "WHERE kind='speculative_research' AND job_id=? "
        "ORDER BY id DESC "
        "LIMIT 1",
        (str(request_id),),
    ).fetchone()
    return dict(row) if row else None


def _launch_speculative_research_subprocess(conn: sqlite3.Connection, request_id: int) -> int:
    """Insert a ``background_tasks`` row, then spawn the research subprocess.

    Used by both POST /ingest/speculative and POST /speculative/regenerate.
    The subprocess reads ``FINDAJOB_BG_TASK_ID`` from env and writes back
    on exit; watchdog reaps stuck rows after 10 minutes per the kind
    timeout. ``job_id`` carries the stringified ``speculative_requests.id``
    since this kind's subject isn't a ``jobs`` row.
    """
    task_id = record_start(conn, job_id=str(request_id), kind="speculative_research")
    script_path = Path(IMAGE_ROOT) / "scripts" / "run_speculative_research.py"
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script_path), str(request_id)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, TASK_ID_ENV_VAR: str(task_id)},
        )
        conn.execute("UPDATE background_tasks SET pid=? WHERE id=?", (proc.pid, task_id))
        conn.commit()
    except Exception as e:
        record_failed(conn, task_id, error_message=f"Popen failed: {e}")
        raise
    return task_id


# ── T21: POST /ingest/speculative ────────────────────────────────────────


@router.post("/ingest/speculative")
def post_speculative(
    company: str = Form(default=""),
    hint: str = Form(default=""),
    personal_notes: str = Form(default=""),
) -> RedirectResponse:
    company = company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="company is required")
    conn = _conn()
    try:
        refusal = check_launch_gate(conn)
        if refusal is not None:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly LLM spend ceiling reached: ${refusal.current_sum_usd:.2f} / "
                    f"${refusal.ceiling_usd:.2f}. Raise or disable the ceiling in /settings/."
                ),
            )
        cur = conn.execute(
            """INSERT INTO speculative_requests (company, hint, personal_notes, status)
               VALUES (?, ?, ?, 'researching')""",
            (company, hint.strip() or None, personal_notes.strip() or None),
        )
        conn.commit()
        request_id = cur.lastrowid
        if request_id is None:  # pragma: no cover — AUTOINCREMENT INSERT always returns lastrowid
            raise RuntimeError("speculative_requests INSERT did not return lastrowid")
        _launch_speculative_research_subprocess(conn, request_id)
    finally:
        conn.close()

    return RedirectResponse(url=f"/speculative/status/{request_id}", status_code=303)


# ── T22: status page + HTMX poll fragment ────────────────────────────────


@router.get("/speculative/status/{request_id}", response_class=HTMLResponse)
def get_status(request: Request, request_id: int):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (request_id,)).fetchone()
        bg_task = _latest_bg_task(conn, request_id) if row is not None else None
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="speculative request not found")
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="speculative/status.html",
        context={"row": dict(row), "bg_task": bg_task},
    )


@router.get("/speculative/status/{request_id}/poll", response_class=HTMLResponse)
def poll_status(request: Request, request_id: int):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (request_id,)).fetchone()
        bg_task = _latest_bg_task(conn, request_id) if row is not None else None
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="speculative/_status_fragment.html",
        context={"row": dict(row), "bg_task": bg_task},
    )


# ── T23: review page ─────────────────────────────────────────────────────


@router.get("/speculative/review/{request_id}", response_class=HTMLResponse)
def get_review(request: Request, request_id: int):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (request_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="speculative request not found")
    if row["status"] != "ready_for_review":
        return RedirectResponse(url=f"/speculative/status/{request_id}", status_code=303)
    cards = parse_role_cards(row["role_cards_json"])
    briefing_html = render_markdown(row["briefing_md"] or "")
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="speculative/review.html",
        context={"row": dict(row), "cards": cards, "briefing_html": briefing_html},
    )


# ── T24: approve / regenerate / trash ────────────────────────────────────


@router.post("/speculative/approve/{request_id}")
def post_approve(request_id: int, keep: Annotated[list[int] | None, Form()] = None) -> RedirectResponse:
    conn = _conn()
    try:
        approve_request(conn, request_id=request_id, kept_indices=keep or [])
    finally:
        conn.close()
    return RedirectResponse(url="/board/dashboard", status_code=303)


@router.post("/speculative/regenerate/{request_id}")
def post_regenerate(request_id: int) -> RedirectResponse:
    conn = _conn()
    try:
        refusal = check_launch_gate(conn)
        if refusal is not None:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly LLM spend ceiling reached: ${refusal.current_sum_usd:.2f} / "
                    f"${refusal.ceiling_usd:.2f}. Raise or disable the ceiling in /settings/."
                ),
            )

        row = conn.execute("SELECT status FROM speculative_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404)
        if row["status"] == "researching":
            raise HTTPException(status_code=409, detail="research already in flight")
        conn.execute(
            """UPDATE speculative_requests
               SET status='researching', error_message=NULL,
                   role_cards_json=NULL, briefing_folder=NULL,
                   research_completed_at=NULL
               WHERE id=?""",
            (request_id,),
        )
        conn.commit()
        _launch_speculative_research_subprocess(conn, request_id)
    finally:
        conn.close()

    return RedirectResponse(url=f"/speculative/status/{request_id}", status_code=303)


@router.post("/speculative/trash/{request_id}")
def post_trash(request_id: int) -> RedirectResponse:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE speculative_requests SET status='trashed' WHERE id=?",
            (request_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(url="/ingest/", status_code=303)
