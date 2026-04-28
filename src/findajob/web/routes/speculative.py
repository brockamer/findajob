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

import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from findajob.paths import BASE
from findajob.speculative.approver import approve_request
from findajob.speculative.parser import parse_role_cards
from findajob.web.markdown import render_markdown

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(BASE) / "src" / "findajob" / "web" / "templates"))

DB_PATH = Path(BASE) / "data" / "pipeline.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


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
        cur = conn.execute(
            """INSERT INTO speculative_requests (company, hint, personal_notes, status)
               VALUES (?, ?, ?, 'researching')""",
            (company, hint.strip() or None, personal_notes.strip() or None),
        )
        conn.commit()
        request_id = cur.lastrowid
    finally:
        conn.close()

    script_path = Path(BASE) / "scripts" / "run_speculative_research.py"
    subprocess.Popen(
        [sys.executable, str(script_path), str(request_id)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return RedirectResponse(url=f"/speculative/status/{request_id}", status_code=303)


# ── T22: status page + HTMX poll fragment ────────────────────────────────


@router.get("/speculative/status/{request_id}", response_class=HTMLResponse)
def get_status(request: Request, request_id: int):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (request_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="speculative request not found")
    return templates.TemplateResponse(request=request, name="speculative/status.html", context={"row": dict(row)})


@router.get("/speculative/status/{request_id}/poll", response_class=HTMLResponse)
def poll_status(request: Request, request_id: int):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (request_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="speculative/_status_fragment.html",
        context={"row": dict(row)},
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
    return templates.TemplateResponse(
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
    return RedirectResponse(url="/board/", status_code=303)


@router.post("/speculative/regenerate/{request_id}")
def post_regenerate(request_id: int) -> RedirectResponse:
    conn = _conn()
    try:
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
    finally:
        conn.close()

    script_path = Path(BASE) / "scripts" / "run_speculative_research.py"
    subprocess.Popen(
        [sys.executable, str(script_path), str(request_id)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
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
