"""GET /onboarding/feed-check/{session_id}/ — validate emitted feed_urls.txt (#984).

Inserted between the interview finalize and the timezone step. After finalize
writes ``config/feed_urls.txt``, this step probes each ATS board URL for liveness
(via the shared :mod:`findajob.fetchers.feed_probe` helper) and surfaces the ones
that couldn't be verified — so a non-technical user never starts with a silently
leaking job funnel. To them a 404 slug is indistinguishable from "no new jobs".

Non-blocking by construction: the page renders instantly with an always-present
Continue link; the probe runs in a separate ``/results`` endpoint that the page
loads asynchronously. A slow or offline ATS can never gate onboarding completion.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.fetchers.feed_probe import probe_feed_urls

router = APIRouter(prefix="/onboarding/feed-check", tags=["onboarding"])


@router.get("/{session_id}/", response_class=HTMLResponse)
def get_feed_check_step(
    session_id: str,
    request: Request,
    voice_redact_failed: int = 0,
) -> HTMLResponse:
    """Render the feed-check page. Results load async; Continue is always live."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/feed_check.html",
        context={
            "session_id": session_id,
            "voice_redact_failed": bool(voice_redact_failed),
        },
    )


@router.get("/{session_id}/results", response_class=HTMLResponse)
def get_feed_check_results(session_id: str, request: Request) -> HTMLResponse:
    """Probe config/feed_urls.txt and render the results panel (HTMX-swapped)."""
    base = Path(request.app.state.base_root)
    feed_urls_path = base / "config" / "feed_urls.txt"
    try:
        lines = feed_urls_path.read_text().splitlines()
    except FileNotFoundError:
        lines = []

    results = probe_feed_urls(lines)
    problems = [r for r in results if r.status != "live"]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/_feed_check_results.html",
        context={
            "session_id": session_id,
            "problems": problems,
            "problem_count": len(problems),
            "total": len(results),
        },
    )
