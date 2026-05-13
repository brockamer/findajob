"""``/tools/`` — guided prompts and direct-edit links (#150).

Phase 1 ships a static tile registry (:mod:`findajob.web.tools_registry`).
Each prompt tile loads its body from ``config/tool_prompts/{slug}.md`` and
renders Copy + Open-in-Claude affordances. Each link tile is a single anchor
to another route in the app.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.web.tools_registry import hydrate_tiles

router = APIRouter()


@router.get("/tools/", response_class=HTMLResponse)
def tools_index(request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/index.html",
        context={"tiles": hydrate_tiles(base_root)},
    )
