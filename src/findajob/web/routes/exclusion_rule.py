"""#653 — per-row 'Add exclusion rule' affordance.

A small cell-level affordance that lets the operator commit a deterministic
exclusion rule straight from a board row, without copy-pasting into a raw text
editor. Two destinations:

  - locus=title → `config/prefilter_rules.yaml` `hard_rejects.operator_added`
    (Stage 1 in scorer_prefilter — title regex → score 1, no LLM call).
  - locus=jd    → `candidate_context/profile.md` `## Excluded Categories`
    (read by the LLM scorer for JD-content signals).

The split is the locus-routing question Decision 1 in #653 flags — title-only
goes to the deterministic prefilter, JD-content goes to the LLM scorer. Wrong
destination = wrong scoring stage.

Three endpoints, mirroring the regenerate-confirm cell-swap pattern (#700):
  - GET  /board/jobs/{fp}/exclude/modal — render form cell
  - POST /board/jobs/{fp}/exclude       — apply, return icon cell
  - GET  /board/jobs/{fp}/exclude/cell  — Cancel-restore icon cell
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.config_loader import (
    ConfigError,
    add_prefilter_title_pattern,
    append_profile_excluded_category,
)
from findajob.web.routes.materials import get_db

router = APIRouter(tags=["board"])


def _fetch_job(db: sqlite3.Connection, fingerprint: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT id, fingerprint, title, company, stage FROM jobs WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()


def _draft_title_pattern(title: str) -> str:
    """Suggest a word-boundary regex for the operator to edit before commit.

    Strategy: lowercase the title, strip seniority/locale noise tokens, then
    wrap the remaining first 2–4 content tokens in `\\b...\\b`. The operator
    edits this in the textarea before saving — the draft is a starting point,
    not a finished rule. Word-boundary anchoring satisfies the #497 invariant
    (substring containment is never the right answer for title matching).
    """
    noise = {
        "senior",
        "sr",
        "sr.",
        "lead",
        "principal",
        "staff",
        "junior",
        "jr",
        "i",
        "ii",
        "iii",
        "iv",
        "remote",
        "us",
        "usa",
    }
    tokens = [t for t in re.split(r"[\s,/\-]+", title.lower()) if t and t not in noise]
    head = tokens[:4] if len(tokens) >= 2 else tokens
    if not head:
        return ""
    return r"\b" + r"\s+".join(re.escape(t) for t in head) + r"\b"


def _render_cell(request: Request, row: sqlite3.Row) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_exclude_cell.html",
        context={"row": row},
    )


def _render_error(request: Request, row: sqlite3.Row, message: str) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_exclude_cell.html",
        context={"row": row, "error": message},
    )


@router.get("/board/jobs/{fingerprint}/exclude/modal", response_class=HTMLResponse)
def exclude_modal(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    row = _fetch_job(db, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    draft = _draft_title_pattern(row["title"] or "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_exclude_modal.html",
        context={"row": row, "draft_pattern": draft},
    )


@router.get("/board/jobs/{fingerprint}/exclude/cell", response_class=HTMLResponse)
def exclude_cell(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    row = _fetch_job(db, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _render_cell(request, row)


@router.post("/board/jobs/{fingerprint}/exclude", response_class=HTMLResponse)
def exclude_apply(
    fingerprint: str,
    request: Request,
    locus: str = Form(""),
    pattern: str = Form(""),
    entry: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    row = _fetch_job(db, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if locus not in ("title", "jd"):
        return _render_error(request, row, "Invalid locus — must be 'title' or 'jd'")

    try:
        if locus == "title":
            add_prefilter_title_pattern(pattern)
        else:
            append_profile_excluded_category(entry)
    except ConfigError as e:
        return _render_error(request, row, str(e))

    return _render_cell(request, row)
