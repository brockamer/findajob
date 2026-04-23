"""In-browser editor for pipeline config files (#149).

Three endpoints:

* ``GET /config/`` — index page, groups editable files by category.
* ``GET /config/files/{path:path}`` — editor view with current content in a textarea.
* ``POST /config/files/{path:path}`` — save handler, returns an HTMX result partial.

The allowlist lives in :mod:`findajob.web.config_files`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.web.config_files import list_editable

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
