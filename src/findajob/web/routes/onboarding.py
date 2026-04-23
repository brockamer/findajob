"""Onboarding NUX: landing page + prompt endpoint + paste-back inject (#148).

Two GET routes land in this task; POST /onboarding/inject is added in Task 7.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter()


def _interview_prompt_path(base_root: Path) -> Path:
    return base_root / "config" / "roles" / "onboarding_interviewer.md"


@router.get("/onboarding/", response_class=HTMLResponse)
def onboarding_index(request: Request, mode: str = "") -> HTMLResponse:
    """Landing page. ``mode=rerun`` flips on the backup warning."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/index.html",
        context={
            "is_rerun": mode == "rerun",
            "paste_error": None,
            "paste_content": "",
        },
    )


@router.get("/onboarding/prompt", response_class=PlainTextResponse)
def onboarding_prompt(request: Request) -> PlainTextResponse:
    """Serve the interview role verbatim so the user can copy it.

    Delivered as ``text/plain; charset=utf-8`` so "copy to clipboard" UX
    is literal — the user pastes the exact bytes we ship.
    """
    base_root: Path = request.app.state.base_root
    prompt_path = _interview_prompt_path(base_root)
    text = prompt_path.read_text(encoding="utf-8")
    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8")
