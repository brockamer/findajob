"""User docs viewer: renders markdown under `docs/` inline at `/docs/`."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.web import constants
from findajob.web.markdown import render_markdown

router = APIRouter()


# Slug → path relative to the repo's `docs/` directory. The four guides named
# in the top-nav index (getting-started, usage, operations, troubleshooting)
# anchor the user-facing doc set; sub-pages within each guide are included so
# cross-links from the guide's README.md resolve in-app instead of 404ing.
_PAGES: dict[str, str] = {
    "usage": "usage.md",
    "usage/expanding-sources": "usage/expanding-sources.md",
    "troubleshooting": "troubleshooting.md",
    "getting-started": "getting-started/README.md",
    "getting-started/prerequisites": "getting-started/prerequisites.md",
    "getting-started/install-docker": "getting-started/install-docker.md",
    "getting-started/configure": "getting-started/configure.md",
    "getting-started/gmail": "getting-started/gmail.md",
    "getting-started/api-keys": "getting-started/api-keys.md",
    "getting-started/notifications": "getting-started/notifications.md",
    "operations": "operations/README.md",
    "operations/internet-exposure": "operations/internet-exposure.md",
    "operations/restore": "operations/restore.md",
}

_INDEX_GUIDES = [
    {
        "slug": "getting-started",
        "title": "Getting started",
        "blurb": "From zero to a running container: prerequisites, Docker install, onboarding, verification.",
    },
    {
        "slug": "usage",
        "title": "Usage",
        "blurb": "Daily workflow — how to drive the pipeline through the web UI, tab by tab.",
    },
    {
        "slug": "operations",
        "title": "Operations",
        "blurb": "Running the stack by hand: manual commands, log rotation, restore, internet exposure.",
    },
    {
        "slug": "troubleshooting",
        "title": "Troubleshooting",
        "blurb": "What the health-check alerts mean and how to unstick common failures.",
    },
]


@router.get("/docs/", response_class=HTMLResponse)
def docs_index(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="docs/index.html",
        context={"guides": _INDEX_GUIDES},
    )


@router.get("/docs/{slug:path}", response_class=HTMLResponse)
def docs_page(slug: str, request: Request) -> HTMLResponse:
    rel = _PAGES.get(slug.rstrip("/"))
    if rel is None:
        raise HTTPException(status_code=404, detail="doc not found")
    base_root: Path = request.app.state.base_root
    docs_root = (base_root / "docs").resolve()
    path = (docs_root / rel).resolve()
    # Defense-in-depth: the allowlist already constrains the path, but keep
    # the traversal guard since `rel` could theoretically contain `..`.
    try:
        path.relative_to(docs_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="doc not found") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    body = path.read_text(encoding="utf-8", errors="replace")
    templates = request.app.state.templates
    rendered_md = render_markdown(body, source=rel)
    if slug == "getting-started/gmail":
        _MARKER = "<!-- gmail-disclosure-sync -->"
        if _MARKER in rendered_md:
            partial_html = templates.get_template("_gmail_disclosure.html").render(
                {"github_blob_url": constants.github_blob_url}
            )
            rendered_md = rendered_md.replace(_MARKER, partial_html, 1)
    return templates.TemplateResponse(
        request=request,
        name="docs/page.html",
        context={
            "slug": slug,
            "rendered_md": rendered_md,
        },
    )
