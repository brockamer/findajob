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

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from findajob.paths import BASE
from findajob.speculative.approver import approve_request

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(BASE) / "src" / "findajob" / "web" / "templates"))

DB_PATH = Path(BASE) / "data" / "pipeline.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn
