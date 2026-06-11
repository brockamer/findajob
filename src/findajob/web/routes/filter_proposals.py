"""Review queue for rejection-driven prefilter proposals (/board/filter-proposals).

Mirrors rejections_review.py. Operator approves (with provenance + preview +
editable regex), skips, or reverts. PATH A panel surfaces the scorer's existing
auto-learning read-only.
"""

from __future__ import annotations

import html
import json
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob import filter_proposals
from findajob.config_loader import ConfigError
from findajob.scoring import _feedback_clusters
from findajob.web.routes.materials import get_db

router = APIRouter()


def _hx_or_redirect(request: Request, status: int = 200) -> HTMLResponse | RedirectResponse:
    if request.headers.get("HX-Request"):
        return HTMLResponse("", status_code=status)
    return RedirectResponse(url="/board/filter-proposals/", status_code=303)


def _item(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "pattern": r["pattern"],
        "source_reason": r["source_reason"],
        "support_count": r["support_count"],
        "support_sample": json.loads(r["support_sample"] or "[]"),
        "preview_count": r["preview_count"],
        "preview_danger_count": r["preview_danger_count"],
        "preview_sample": json.loads(r["preview_sample"] or "[]"),
    }


def _path_a(db: sqlite3.Connection) -> list[dict]:
    clusters = _feedback_clusters(db)
    out = []
    for reason, titles in sorted(clusters.items(), key=lambda x: -len(x[1])):
        unique = list(dict.fromkeys(titles))
        out.append({"reason": reason, "count": len(unique), "sample": unique[:6]})
    return out


@router.get("/board/filter-proposals/", response_class=HTMLResponse)
def index(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    rows = db.execute(
        "SELECT * FROM filter_proposals WHERE status='pending' ORDER BY created_at DESC, id DESC"
    ).fetchall()
    items = [_item(r) for r in rows]
    applied_rows = db.execute(
        "SELECT * FROM filter_proposals WHERE status='applied' ORDER BY decided_at DESC, id DESC LIMIT 20"
    ).fetchall()
    applied_items = [
        {"id": r["id"], "pattern": r["pattern"], "affected": len(json.loads(r["affected_jobs"] or "[]"))}
        for r in applied_rows
    ]
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="filter_proposals.html",
        context={"items": items, "pending_count": len(items), "path_a": _path_a(db), "applied_items": applied_items},
    )


@router.get("/board/filter-proposals/widget", response_class=HTMLResponse)
def widget(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    n = db.execute("SELECT COUNT(*) FROM filter_proposals WHERE status='pending'").fetchone()[0]
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="board/_filter_proposals_widget.html",
        context={"filter_proposals_pending": n},
    )


@router.get("/board/filter-proposals/{proposal_id}/card", response_class=HTMLResponse)
def card(
    proposal_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Cancel-restore: re-render a single pending card."""
    r = db.execute("SELECT * FROM filter_proposals WHERE id=? AND status='pending'", (proposal_id,)).fetchone()
    if r is None:
        return HTMLResponse("", status_code=200)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="board/_filter_proposal_card.html",
        context={"it": _item(r)},
    )


@router.post("/board/filter-proposals/{proposal_id}/apply", response_model=None)
def apply(
    proposal_id: int,
    request: Request,
    pattern: str = Form(""),
    confirm: str = Form(""),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    try:
        result = filter_proposals.apply_proposal(db, proposal_id, pattern, force=(confirm == "1"))
    except ConfigError as e:
        return HTMLResponse(f'<div class="text-rose-700 text-xs p-2">{html.escape(str(e))}</div>', status_code=200)
    if result.get("result") == "needs_confirm":
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="board/_filter_proposal_confirm.html",
            context={
                "proposal_id": proposal_id,
                "pattern": pattern,
                "danger_count": result["danger_count"],
                "danger_sample": result["danger_sample"],
            },
        )
    return _hx_or_redirect(request)


@router.post("/board/filter-proposals/{proposal_id}/skip", response_model=None)
def skip(
    proposal_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    filter_proposals.skip_proposal(db, proposal_id)
    return _hx_or_redirect(request)


@router.post("/board/filter-proposals/{proposal_id}/revert", response_model=None)
def revert(
    proposal_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    filter_proposals.revert_proposal(db, proposal_id)
    return _hx_or_redirect(request)
