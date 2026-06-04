"""#985: /settings/feed-urls/ — verify configured ATS feeds are live.

Verify-only view (editing stays in the raw /config/ editor). GET lists the
operator's configured feeds; POST /verify probes them all via the shared
`findajob.fetchers.feed_probe` helper (#1023, also used by #983/#984) and
HTMX-swaps in per-row live/dead/unreachable/unsupported badges with reasons.

The shared helper couples parse + probe (every call hits the network), so the
pre-Verify GET listing does its own display-only split — it shows each raw feed
line with the inline ``# comment`` as a label where present. ATS classification,
company name, and status come from the probe on Verify, not from this listing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.fetchers import feed_probe

router = APIRouter(prefix="/settings/feed-urls", tags=["settings"])


def _feed_lines(request: Request) -> tuple[list[str], bool]:
    """Read the operator's runtime feed_urls.txt via app.state.base_root.
    Returns (lines, file_present); ([], False) when the file is absent."""
    base_root = Path(request.app.state.base_root)
    path = base_root / "config" / "feed_urls.txt"
    if not path.exists():
        return [], False
    return path.read_text(encoding="utf-8", errors="replace").splitlines(), True


def _display_rows(lines: list[str]) -> list[dict[str, str]]:
    """Pre-Verify display rows (no probing): skip blank/comment lines, surface
    the URL plus the inline ``# comment`` as a label where present. Pure display
    formatting — no ATS classification, no network."""
    rows: list[dict[str, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url_part, _, comment = line.partition("#")
        rows.append({"url": url_part.strip(), "label": comment.strip()})
    return rows


@router.get("/", response_class=HTMLResponse)
def get_feed_urls_page(request: Request) -> HTMLResponse:
    lines, file_present = _feed_lines(request)
    rows = _display_rows(lines)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/feed_urls.html",
        context={"rows": rows, "file_present": file_present},
    )


@router.post("/verify", response_class=HTMLResponse)
def post_verify_feed_urls(request: Request) -> HTMLResponse:
    lines, _ = _feed_lines(request)
    results = feed_probe.probe_feed_urls(lines)
    all_unreachable = bool(results) and all(r.status == "unreachable" for r in results)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/_feed_urls_verify_result.html",
        context={"results": results, "all_unreachable": all_unreachable},
    )
