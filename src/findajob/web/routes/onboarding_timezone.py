"""GET + POST /onboarding/timezone/{session_id}/ — deterministic timezone capture (#989).

Inserted between the interview finalize and the spend-ceiling step. The
operator's browser resolves the IANA zone client-side
(``Intl.DateTimeFormat().resolvedOptions().timeZone``) and pre-selects the
picker; the server-side default falls back to whatever the LLM already wrote to
``data/timezone`` at finalize, so the LLM-conversion path is preserved when the
browser value is unavailable or unresolvable. The confirmed pick is written
atomically via :func:`findajob.timeutil.write_timezone_file` and supersedes the
LLM value.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import available_timezones

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from findajob.timeutil import (
    is_valid_timezone,
    local_tz,
    read_timezone_file,
    write_timezone_file,
)

router = APIRouter(prefix="/onboarding/timezone", tags=["onboarding"])


def _sorted_zones() -> list[str]:
    """All IANA zone names, sorted, for the picker."""
    return sorted(available_timezones())


def _next_url(session_id: str, voice_redact_failed: bool) -> str:
    """The spend-ceiling step is next; propagate the one-shot redact flag."""
    redact = "?voice_redact_failed=1" if voice_redact_failed else ""
    return f"/onboarding/spend-ceiling/{session_id}/{redact}"


@router.get("/{session_id}/", response_class=HTMLResponse)
def get_timezone_step(
    session_id: str,
    request: Request,
    voice_redact_failed: int = 0,
) -> HTMLResponse:
    """Render the timezone confirmation step with the LLM pick as the default."""
    base = Path(request.app.state.base_root)
    default_tz = read_timezone_file(base) or local_tz()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/timezone.html",
        context={
            "session_id": session_id,
            "default_tz": default_tz,
            "zones": _sorted_zones(),
            "voice_redact_failed": bool(voice_redact_failed),
        },
    )


@router.post("/{session_id}/", response_class=HTMLResponse)
async def post_timezone_step(
    session_id: str,
    request: Request,
    timezone: str = Form(default=""),
    voice_redact_failed: int = Form(default=0),
) -> Response:
    """Persist a confirmed zone, then advance to the spend-ceiling step.

    Blank or unresolvable input is a deliberate no-op: it keeps whatever the
    interview wrote to ``data/timezone`` (the LLM-conversion fallback) rather
    than clobbering a good value with garbage. The flow always proceeds.
    """
    base = Path(request.app.state.base_root)
    candidate = timezone.strip()
    if is_valid_timezone(candidate):
        write_timezone_file(base, candidate)
    return RedirectResponse(_next_url(session_id, bool(voice_redact_failed)), status_code=303)
