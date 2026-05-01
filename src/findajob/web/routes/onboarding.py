"""Onboarding NUX: landing page + prompt endpoint + paste-back inject (#148)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from findajob.onboarding import OnboardingSmokeCheckFailed, inject, parse_emission
from findajob.onboarding.parser import ALLOWED_FILENAMES

router = APIRouter()


def _interview_prompt_path(base_root: Path) -> Path:
    return base_root / "config" / "roles" / "onboarding_interviewer.md"


def _format_parse_error(
    emission: str,
    missing: list[str],
    unknown: list[str],
) -> str:
    """Build a diagnostic error message for a failed emission parse.

    Distinguishes the three real-world failure shapes — empty paste, no
    delimited blocks at all (=> wrong content type), and partial paste
    with some blocks present — so the user gets a remedy specific to
    what actually went wrong, not a generic 'something is missing'.
    Surfaces unknown block names (likely typos) as a separate hint.
    """
    blob = emission.strip()
    found_count = len(ALLOWED_FILENAMES) - len(missing)

    if not blob:
        msg = (
            "The paste box is empty. After your LLM finishes the interview "
            "and emits the file blocks, copy the entire chat (or at least "
            "everything from the first `<<<FILE: …>>>` line to the last "
            "`<<<END FILE: …>>>` line) and paste it here."
        )
    elif found_count == 0:
        # Pasted SOMETHING, but parser found zero recognizable blocks.
        # Most often: copied just the chat-prose, missed the delimited blocks;
        # or LLM produced markdown headings instead of `<<<FILE: …>>>` markers.
        msg = (
            "We couldn't find any `<<<FILE: name>>>` … `<<<END FILE: name>>>` "
            "block in your paste. Common causes: (a) the LLM didn't actually "
            'emit the delimited file blocks — re-prompt it with "Now emit the '
            'ten file blocks per the interview spec"; (b) you copied only '
            "the chat prose and missed the blocks at the end of the "
            "transcript; (c) markdown formatting stripped the `<<<` markers — "
            "try copying from the LLM's raw-text view if it has one."
        )
    else:
        # Some blocks parsed, others didn't. Likely the LLM stopped emitting
        # mid-list, or the user's paste was truncated.
        msg = (
            f"We found {found_count} of {len(ALLOWED_FILENAMES)} required "
            "blocks, but these are still missing: "
            f"{', '.join(missing)}. Scroll through your chat to make sure "
            "every `<<<FILE: name>>> … <<<END FILE: name>>>` block is in "
            "your paste — re-prompt the LLM if it stopped early."
        )

    if unknown:
        msg += (
            f" We also found these unrecognized block names (likely typos): "
            f"{', '.join(unknown)}. If one of those was supposed to be a "
            "required block, fix the filename in your paste and re-submit."
        )

    return msg


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
        paste_error = _format_parse_error(emission, result.missing, result.unknown)
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": openrouter_api_key,
                "paste_error": paste_error,
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
                    "OpenRouter API key is missing. Paste your key (starts with sk-or-v1-…) "
                    "from https://openrouter.ai/keys into the API key field above the paste box, "
                    "then click Inject again. The key is required so we can verify it works "
                    "before sealing your stack — failing to verify here would let the pipeline "
                    "silently break on first scheduled triage."
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
        # error so they can see what went wrong. e.user_message is already a
        # specific, actionable string — surface it without further wrapping.
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": openrouter_api_key,
                "paste_error": (
                    "OpenRouter rejected the key when we tried to verify it. "
                    f"{e.user_message} "
                    "Fix the key and click Inject again — your paste content is preserved above."
                ),
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
