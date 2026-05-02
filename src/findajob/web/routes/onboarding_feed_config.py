"""GET /onboarding/feed-config/{session_id} — per-adapter signup walkthrough (#408).

Uses ``request.app.state.base_root`` (set by ``create_app``) instead of the
module-level ``BASE`` constant, so the path resolves correctly in tests that
pass an isolated ``tmp_path`` via ``base_root=...`` to ``create_app``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob.fetchers.adapters.curation import (
    AdapterMetadata,
    CurationLoadError,
    load_curation,
)
from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS, _read_active_sources

router = APIRouter(prefix="/onboarding/feed-config", tags=["onboarding"])


def _resolve_adapter_metadata(base: Path) -> AdapterMetadata:
    """Return the metadata for the currently-active adapter that needs configuring.

    Raises HTTPException 404 if ``config/active_sources.txt`` is absent (the
    backwards-compat default in ``_read_active_sources`` would otherwise hide
    the missing-file case).  Raises HTTPException 500 on curation YAML errors.
    """
    active_sources_file = base / "config" / "active_sources.txt"
    if not active_sources_file.exists():
        raise HTTPException(status_code=404, detail="No active source pending configuration.")

    active = _read_active_sources(path=active_sources_file)
    if not active:
        raise HTTPException(status_code=404, detail="No active source pending configuration.")

    try:
        cur = load_curation(base / "config" / "rapidapi_feeds.yaml")
    except CurationLoadError as e:
        raise HTTPException(status_code=500, detail=f"Curation load failed: {e}") from e

    registered_names = {cls.name for cls in REGISTERED_ADAPTERS}
    for name in active:
        if name not in registered_names:
            continue
        meta = cur.adapter_by_name(name)
        if meta is not None:
            return meta

    raise HTTPException(status_code=404, detail="No matching adapter metadata found.")


@router.get("/{session_id}", response_class=HTMLResponse)
def get_feed_config_form(session_id: str, request: Request) -> HTMLResponse:
    base = Path(request.app.state.base_root)
    meta = _resolve_adapter_metadata(base)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding_feed_config/index.html",
        context={
            "session_id": session_id,
            "adapter": meta,
        },
    )
