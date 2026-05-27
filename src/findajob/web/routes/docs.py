"""User docs viewer: renders markdown under `docs/` inline at `/docs/`."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.web.markdown import render_markdown

router = APIRouter()


_PAGES: dict[str, str] = {
    # Getting started
    "getting-started": "getting-started/README.md",
    "getting-started/start-here-fly": "getting-started/start-here-fly.md",
    "getting-started/install-fly": "getting-started/install-fly.md",
    "getting-started/api-keys": "getting-started/api-keys.md",
    "getting-started/cost": "getting-started/cost.md",
    "getting-started/gmail": "getting-started/gmail.md",
    "getting-started/notifications": "getting-started/notifications.md",
    # Daily use
    "usage": "usage.md",
    "usage/expanding-sources": "usage/expanding-sources.md",
    "tuning": "tuning.md",
    "troubleshooting": "troubleshooting.md",
    # Operations
    "operations": "operations/README.md",
    "operations/fly-deploy": "operations/fly-deploy.md",
    "operations/install-docker": "operations/install-docker.md",
    "operations/config-reference": "operations/config-reference.md",
    "operations/internet-exposure": "operations/internet-exposure.md",
    "operations/restore": "operations/restore.md",
}

_SEQUENCE: dict[str, str] = {
    "getting-started/start-here-fly": "getting-started/install-fly",
    "getting-started/install-fly": "getting-started/api-keys",
    "getting-started/api-keys": "getting-started/cost",
    "getting-started/cost": "getting-started/gmail",
    "getting-started/gmail": "getting-started/notifications",
}

_BREADCRUMB_LABELS: dict[str, str] = {
    "getting-started": "Getting Started",
    "usage": "Usage",
    "operations": "Operations",
    "troubleshooting": "Troubleshooting",
    "tuning": "Tuning",
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


def _build_breadcrumbs(slug: str) -> list[dict[str, str]]:
    """Build breadcrumb trail: Docs > Section > Page."""
    crumbs: list[dict[str, str]] = [{"label": "Docs", "href": "/docs/"}]
    parts = slug.split("/")
    if len(parts) >= 1 and parts[0] in _BREADCRUMB_LABELS:
        section_slug = parts[0]
        crumbs.append(
            {
                "label": _BREADCRUMB_LABELS[section_slug],
                "href": f"/docs/{section_slug}",
            }
        )
    if len(parts) >= 2:
        title = parts[-1].replace("-", " ").title()
        crumbs.append({"label": title, "href": ""})
    return crumbs


@router.get("/docs/{slug:path}", response_class=HTMLResponse)
def docs_page(slug: str, request: Request) -> HTMLResponse:
    slug = slug.rstrip("/")
    rel = _PAGES.get(slug)
    if rel is None:
        raise HTTPException(status_code=404, detail="doc not found")
    image_root: Path = request.app.state.image_root
    docs_root = (image_root / "docs").resolve()
    path = (docs_root / rel).resolve()
    try:
        path.relative_to(docs_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="doc not found") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    body = path.read_text(encoding="utf-8", errors="replace")
    templates = request.app.state.templates
    rendered_md = render_markdown(body, source=rel)

    next_slug = _SEQUENCE.get(slug)
    next_step = None
    if next_slug:
        next_title = next_slug.split("/")[-1].replace("-", " ").title()
        next_step = {"href": f"/docs/{next_slug}", "title": next_title}

    return templates.TemplateResponse(
        request=request,
        name="docs/page.html",
        context={
            "slug": slug,
            "rendered_md": rendered_md,
            "breadcrumbs": _build_breadcrumbs(slug),
            "next_step": next_step,
        },
    )
