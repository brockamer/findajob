"""In-browser editor for pipeline config files (#149).

Three endpoints:

* ``GET /config/`` — index page, groups editable files by category.
* ``GET /config/files/{path:path}`` — editor view with current content in a textarea.
* ``POST /config/files/{path:path}`` — save handler, returns an HTMX result partial.

The allowlist lives in :mod:`findajob.web.config_files`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.web.config_files import list_editable, resolve_editable

router = APIRouter()


@router.get("/config/", response_class=HTMLResponse)
def config_index(request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    categories = list_editable(base_root)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/index.html",
        context={"categories": categories},
    )


@router.get("/config/files/{relpath:path}", response_class=HTMLResponse)
def config_edit_form(relpath: str, request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    resolved = resolve_editable(relpath, base_root)
    if resolved is None:
        raise HTTPException(status_code=403, detail="file is not editable")

    content = ""
    exists = resolved.is_file()
    if exists:
        content = resolved.read_text(encoding="utf-8", errors="replace")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/editor.html",
        context={"relpath": relpath, "content": content, "exists": exists},
    )
