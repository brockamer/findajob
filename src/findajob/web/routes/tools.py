"""Placeholder ``/tools/`` landing page (#149).

Bumped from a "coming soon" placeholder to a real route so #149's AC
"editor is linked from /tools/ as the 'edit config files' action" can be
satisfied. Future tools (doctor, scoreboard, etc.) extend this template.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/tools/", response_class=HTMLResponse)
def tools_index(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/index.html",
        context={},
    )
