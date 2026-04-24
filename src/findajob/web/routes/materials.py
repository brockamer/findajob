"""Materials viewer routes: /, /materials/{fp}, /materials/{fp}/{file}."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from findajob.web.folder_resolver import resolve_folder
from findajob.web.markdown import render_markdown

router = APIRouter()


def get_db() -> sqlite3.Connection:  # pragma: no cover — overridden in app factory
    raise NotImplementedError("DB dependency must be overridden by create_app()")


_INDEX_QUERY_SECTIONS = [
    ("In flight", "stage IN ('materials_drafted', 'prep_in_progress')", "created_at DESC"),
    ("Applied", "stage IN ('applied', 'interview', 'offer')", "COALESCE(stage_updated, created_at) DESC"),
    ("Waitlisted", "stage = 'waitlisted'", "created_at DESC"),
]
_REJECTED_CLAUSE = "stage IN ('rejected', 'not_selected')"
_PER_SECTION_CAP = 50


def _fetch_section(db: sqlite3.Connection, where: str, order: str) -> list[sqlite3.Row]:
    return db.execute(
        f"SELECT fingerprint, title, company, stage, fit_score, created_at, stage_updated "
        f"FROM jobs WHERE {where} ORDER BY {order} LIMIT {_PER_SECTION_CAP + 1}"
    ).fetchall()


@router.get("/materials/", response_class=HTMLResponse)
def materials_index(request: Request, db: sqlite3.Connection = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    sections = []
    for name, where, order in _INDEX_QUERY_SECTIONS:
        rows = _fetch_section(db, where, order)
        overflow = len(rows) > _PER_SECTION_CAP
        sections.append({"name": name, "rows": rows[:_PER_SECTION_CAP], "overflow": overflow})
    rejected_rows = _fetch_section(db, _REJECTED_CLAUSE, "created_at DESC")
    rejected = {
        "rows": rejected_rows[:_PER_SECTION_CAP],
        "overflow": len(rejected_rows) > _PER_SECTION_CAP,
        "count": len(rejected_rows) if len(rejected_rows) <= _PER_SECTION_CAP else f"{_PER_SECTION_CAP}+",
    }
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="materials/index.html",
        context={"sections": sections, "rejected": rejected},
    )


@router.get("/materials/{fingerprint}", response_class=HTMLResponse)
def folder_view(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    root: Path = request.app.state.companies_root
    folder = resolve_folder(fingerprint, db, root)
    if folder is None:
        raise HTTPException(status_code=404, detail="folder not found")
    row = db.execute("SELECT title, company, stage FROM jobs WHERE fingerprint = ?", (fingerprint,)).fetchone()
    files = sorted(p.name for p in folder.iterdir() if p.is_file())
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="materials/folder.html",
        context={
            "fingerprint": fingerprint,
            "folder_name": folder.name,
            "title": row["title"] if row else "",
            "company": row["company"] if row else "",
            "stage": row["stage"] if row else "",
            "files": files,
        },
    )


@router.get("/materials/{fingerprint}/{filename}")
def file_serve(
    fingerprint: str,
    filename: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
):
    root: Path = request.app.state.companies_root
    folder = resolve_folder(fingerprint, db, root)
    if folder is None:
        raise HTTPException(status_code=404, detail="folder not found")
    candidate = (folder / filename).resolve()
    try:
        candidate.relative_to(folder.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="invalid filename") from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    ext = candidate.suffix.lower()
    if ext == ".md":
        body = candidate.read_text(encoding="utf-8", errors="replace")
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="base.html",
            context={"_rendered_md": render_markdown(body)},
            headers={"content-type": "text/html; charset=utf-8"},
        )
    if ext == ".txt":
        return PlainTextResponse(content=candidate.read_text(encoding="utf-8", errors="replace"))
    return FileResponse(
        path=candidate,
        filename=candidate.name,
        media_type="application/octet-stream",
        headers={"content-disposition": f'attachment; filename="{candidate.name}"'},
    )
