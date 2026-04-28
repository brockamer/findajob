"""Onboarding NUX: landing page + prompt endpoint + paste-back inject (#148)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from findajob.onboarding import OnboardingSmokeCheckFailed, inject, parse_emission

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
            "openrouter_api_key": "",
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
    openrouter_api_key: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Parse and inject an interview emission; render completion page on success.

    The OpenRouter API key arrives in its own form field — kept out of the
    ``emission`` blob so it never enters the user's chat-LLM logs (#328).
    """
    templates = request.app.state.templates
    result = parse_emission(emission)
    if result.missing:
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": openrouter_api_key,
                "paste_error": (
                    f"Your paste is missing: {', '.join(result.missing)}. "
                    "Scroll through your chat for any <<<FILE: name>>> block "
                    "that's not in your paste and include it."
                ),
            },
            status_code=400,
        )
    if not openrouter_api_key.strip():
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": "",
                "paste_error": (
                    "OpenRouter API key is required. Paste the key from https://openrouter.ai/ into the API key field."
                ),
            },
            status_code=400,
        )
    base_root: Path = request.app.state.base_root
    try:
        inject_result = inject(base_root, result.found, openrouter_api_key=openrouter_api_key)
    except OnboardingSmokeCheckFailed as e:
        # Files were committed; only the sentinel is missing. The next paste-back
        # with a corrected key will overwrite cleanly. Render the user-facing
        # error so they can see what went wrong.
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": openrouter_api_key,
                "paste_error": (f"OpenRouter key check failed: {e.user_message} Fix the key and re-paste."),
            },
            status_code=400,
        )
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
