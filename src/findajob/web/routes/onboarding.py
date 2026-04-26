"""Onboarding NUX: landing page + prompt endpoint + paste-back inject (#148)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from findajob.onboarding import inject, parse_emission

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


@router.post("/onboarding/inject", response_model=None)
def onboarding_inject(
    request: Request,
    emission: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Parse and inject an interview emission; render completion page on success."""
    result = parse_emission(emission)
    templates = request.app.state.templates
    if result.missing:
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "paste_error": (
                    f"Your paste is missing: {', '.join(result.missing)}. "
                    "Scroll through your chat for any <<<FILE: name>>> block "
                    "that's not in your paste and include it."
                ),
            },
            status_code=400,
        )
    base_root: Path = request.app.state.base_root
    inject_result = inject(base_root, result.found)
    # Clear cached guard state so the next /board/ request passes through
    request.app.state.onboarding_complete = True
    return templates.TemplateResponse(
        request=request,
        name="onboarding/complete.html",
        context={
            "discovery_success": inject_result.discovery.success,
            "discovery_count": inject_result.discovery.count,
            "discovery_error": inject_result.discovery.error,
        },
    )
