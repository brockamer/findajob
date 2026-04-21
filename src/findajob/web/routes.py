"""Route handlers for the materials viewer."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from findajob.web.folder_resolver import resolve_folder

router = APIRouter()


def get_db() -> sqlite3.Connection:  # pragma: no cover — overridden in app factory
    raise NotImplementedError("DB dependency must be overridden by create_app()")


@router.get("/healthz", response_class=Response)
def healthz(request: Request) -> Response:
    root: Path = request.app.state.companies_root
    if not root.is_dir():
        return Response(content="companies/ missing", status_code=503, media_type="text/plain")
    return Response(content="ok", status_code=200, media_type="text/plain")


@router.get("/materials/{fingerprint}", response_class=HTMLResponse)
def folder_view(
    fingerprint: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    root: Path = request.app.state.companies_root
    folder = resolve_folder(fingerprint, db, root)
    if folder is None:
        raise HTTPException(status_code=404, detail="folder not found")

    row = db.execute(
        "SELECT title, company, stage FROM jobs WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()

    files = sorted(p.name for p in folder.iterdir() if p.is_file())
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="folder.html",
        context={
            "fingerprint": fingerprint,
            "folder_name": folder.name,
            "title": row["title"] if row else "",
            "company": row["company"] if row else "",
            "stage": row["stage"] if row else "",
            "files": files,
        },
    )
