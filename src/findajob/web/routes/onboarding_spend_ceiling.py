"""GET + POST /onboarding/spend-ceiling/{session_id} — spend ceiling onboarding step (#671).

Inserted between the interview finalize step and the existing feed-config /
gmail-config decision.  The user sets a monthly LLM spend ceiling (or skips);
then /finish re-derives the feed-config vs gmail-config gate decision and
redirects accordingly.

Uses ``request.app.state.base_root`` for config path resolution so tests that
pass an isolated ``tmp_path`` via ``base_root=...`` to ``create_app`` get
automatic file-level isolation.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from findajob.onboarding.injector import decide_post_interview_redirect
from findajob.web.routes.settings_spend_ceiling import (
    _APPLIES_PER_WEEK_OPTIONS,
    _DEFAULT_APPLIES_PER_WEEK,
    PER_PREP_USD,
    SCORING_FLOOR_USD,
    _recommended_ceiling,
)

router = APIRouter(prefix="/onboarding/spend-ceiling", tags=["onboarding"])


def _ceiling_path(base_root: Path) -> Path:
    return base_root / "config" / "spend_ceiling.txt"


def _write_ceiling(path: Path, content: str) -> None:
    """Atomically write content to path via tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, str(path))
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _post_spend_ceiling_redirect(session_id: str, base_root: Path, voice_redact_failed: bool) -> str:
    """Derive the next-step URL after the spend-ceiling onboarding step.

    Mirrors the logic from ``finalize_interview`` — re-derives the feed-config
    vs gmail-config gate from disk state via ``decide_post_interview_redirect``.
    """
    redact_param = "?voice_redact_failed=1" if voice_redact_failed else ""
    decision = decide_post_interview_redirect(base_root)
    if decision.gate_to_feed_config:
        return f"/onboarding/feed-config/{session_id}{redact_param}"
    return f"/onboarding/gmail-config/{session_id}/{redact_param}"


@router.get("/{session_id}/", response_class=HTMLResponse)
def get_spend_ceiling_step(
    session_id: str,
    request: Request,
    voice_redact_failed: int = 0,
) -> HTMLResponse:
    """Render the spend-ceiling onboarding form with a recommendation pre-filled."""
    default_ceiling = _recommended_ceiling(_DEFAULT_APPLIES_PER_WEEK)
    recommendations = {n: _recommended_ceiling(n) for n in _APPLIES_PER_WEEK_OPTIONS}
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/spend_ceiling.html",
        context={
            "session_id": session_id,
            "applies_options": _APPLIES_PER_WEEK_OPTIONS,
            "default_applies": _DEFAULT_APPLIES_PER_WEEK,
            "default_ceiling": default_ceiling,
            "recommendations": recommendations,
            "scoring_floor_usd": SCORING_FLOOR_USD,
            "per_prep_usd": PER_PREP_USD,
            "voice_redact_failed": bool(voice_redact_failed),
        },
    )


@router.post("/{session_id}/", response_class=HTMLResponse)
async def post_spend_ceiling_step(
    session_id: str,
    request: Request,
    action: str = Form(default="save"),
    ceiling_override: str = Form(default=""),
    applies_per_week: int = Form(default=_DEFAULT_APPLIES_PER_WEEK),
    voice_redact_failed: int = Form(default=0),
) -> Response:
    """Write ceiling (or skip) then redirect to /finish."""
    base_root = Path(request.app.state.base_root)
    path = _ceiling_path(base_root)

    if action == "skip":
        # Skip: do not write the file so load_spend_ceiling() returns None and
        # the dashboard banner appears pointing operators to the settings page.
        return RedirectResponse(
            f"/onboarding/spend-ceiling/{session_id}/finish?voice_redact_failed={voice_redact_failed}",
            status_code=303,
        )

    raw = ceiling_override.strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = None
        if value is None or value <= 0:
            # Re-render with error.
            templates = request.app.state.templates
            return templates.TemplateResponse(
                request=request,
                name="onboarding/spend_ceiling.html",
                context={
                    "session_id": session_id,
                    "applies_options": _APPLIES_PER_WEEK_OPTIONS,
                    "default_applies": applies_per_week,
                    "default_ceiling": _recommended_ceiling(applies_per_week),
                    "recommendations": {n: _recommended_ceiling(n) for n in _APPLIES_PER_WEEK_OPTIONS},
                    "scoring_floor_usd": SCORING_FLOOR_USD,
                    "per_prep_usd": PER_PREP_USD,
                    "voice_redact_failed": bool(voice_redact_failed),
                    "error": f"Invalid ceiling: {ceiling_override!r}. Enter a positive number (e.g. 50).",
                },
                status_code=400,
            )
    else:
        if applies_per_week not in _APPLIES_PER_WEEK_OPTIONS:
            applies_per_week = _DEFAULT_APPLIES_PER_WEEK
        value = _recommended_ceiling(applies_per_week)

    _write_ceiling(path, f"{value:.2f}\n")
    return RedirectResponse(
        f"/onboarding/spend-ceiling/{session_id}/finish?voice_redact_failed={voice_redact_failed}",
        status_code=303,
    )


@router.get("/{session_id}/finish")
def get_finish(
    session_id: str,
    request: Request,
    voice_redact_failed: int = 0,
) -> Response:
    """Perform the feed-config vs gmail-config decision and redirect."""
    base_root = Path(request.app.state.base_root)
    next_url = _post_spend_ceiling_redirect(session_id, base_root, bool(voice_redact_failed))
    return RedirectResponse(next_url, status_code=303)
