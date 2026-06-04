"""#985: /settings/feed-urls/ — verify configured ATS feeds are live.

Verify-only view (editing stays in the raw /config/ editor). GET lists the
operator's configured feeds; POST /verify probes them all concurrently and
HTMX-swaps in per-row live/dead/unreachable/unsupported badges with fix hints.
Probe logic lives in `findajob.fetchers.feed_probe` and is shared with #984.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.fetchers import feed_probe

router = APIRouter(prefix="/settings/feed-urls", tags=["settings"])


def _feed_urls_text(request: Request) -> tuple[str, bool]:
    """Read the operator's runtime feed_urls.txt via app.state.base_root.
    Returns (text, file_present); empty text + False when absent."""
    base_root = Path(request.app.state.base_root)
    path = base_root / "config" / "feed_urls.txt"
    if not path.exists():
        return "", False
    return path.read_text(encoding="utf-8", errors="replace"), True


@router.get("/", response_class=HTMLResponse)
def get_feed_urls_page(request: Request) -> HTMLResponse:
    text, file_present = _feed_urls_text(request)
    rows = feed_probe.parse_feed_rows(text)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/feed_urls.html",
        context={"rows": rows, "file_present": file_present},
    )


@router.post("/verify", response_class=HTMLResponse)
def post_verify_feed_urls(request: Request) -> HTMLResponse:
    text, _ = _feed_urls_text(request)
    rows = feed_probe.parse_feed_rows(text)
    results = feed_probe.probe_all(rows)
    all_unreachable = bool(results) and all(r.status == "unreachable" for r in results)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/_feed_urls_verify_result.html",
        context={"results": results, "all_unreachable": all_unreachable},
    )
