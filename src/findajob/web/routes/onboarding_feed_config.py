"""GET + POST /onboarding/feed-config/{session_id} — per-adapter signup walkthrough (#408).

Uses ``request.app.state.base_root`` (set by ``create_app``) instead of the
module-level ``BASE`` constant, so the path resolves correctly in tests that
pass an isolated ``tmp_path`` via ``base_root=...`` to ``create_app``.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from findajob.env_file import check_value, set_vars
from findajob.fetchers.adapters.curation import (
    AdapterMetadata,
    CurationLoadError,
    load_curation,
)
from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS, _read_active_sources

router = APIRouter(prefix="/onboarding/feed-config", tags=["onboarding"])

# Map adapter name → adapter class for instantiation
_ADAPTER_CLASSES = {cls.name: cls for cls in REGISTERED_ADAPTERS}


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


def _read_queries(base: Path) -> list[str]:
    """Read search queries from config/jsearch_queries.txt, stripping comments/blanks."""
    queries_path = base / "config" / "jsearch_queries.txt"
    if not queries_path.exists():
        return []
    return [line.strip() for line in queries_path.read_text().splitlines() if line.strip() and not line.startswith("#")]


@router.get("/{session_id}", response_class=HTMLResponse)
def get_feed_config_form(session_id: str, request: Request, voice_redact_failed: int = 0) -> HTMLResponse:
    base = Path(request.app.state.base_root)
    meta = _resolve_adapter_metadata(base)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding_feed_config/index.html",
        context={
            "session_id": session_id,
            "adapter": meta,
            "voice_redact_failed": bool(voice_redact_failed),
        },
    )


@router.post("/{session_id}", response_class=HTMLResponse)
def post_feed_config(
    session_id: str,
    request: Request,
    api_key: str | None = Form(default=None),
    skip: str | None = Form(default=None),
) -> HTMLResponse:
    base = Path(request.app.state.base_root)
    meta = _resolve_adapter_metadata(base)
    templates = request.app.state.templates

    if skip:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_feed_config/_live_test_result.html",
            context={
                "session_id": session_id,
                "adapter": meta,
                "skipped": True,
                "result": None,
            },
        )

    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required (or use 'Skip for now').")

    adapter_cls = _ADAPTER_CLASSES.get(meta.name)
    if adapter_cls is None:
        raise HTTPException(status_code=500, detail=f"Adapter {meta.name} not registered.")

    # Set env var in-process so the adapter can pick it up, then run the live test.
    env_var = meta.required_env_var
    try:
        check_value(env_var, api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="API key cannot contain line breaks.") from exc
    os.environ[env_var] = api_key

    queries = _read_queries(base)
    adapter = adapter_cls()
    result = adapter.live_test(queries)

    if result.ok:
        # Live test succeeded — persist the key for future sessions.
        set_vars(base / "data" / ".env", {env_var: api_key})
    else:
        # Failure — do NOT persist the key; roll back env mutation.
        os.environ.pop(env_var, None)

    return templates.TemplateResponse(
        request=request,
        name="onboarding_feed_config/_live_test_result.html",
        context={
            "session_id": session_id,
            "adapter": meta,
            "skipped": False,
            "result": result,
        },
    )


@router.post("/{session_id}/finish")
def post_finish(session_id: str, request: Request) -> Response:
    """Hand off to the Gmail-config gate (#407).

    The sentinel is no longer written here — every onboarding flow now ends at
    ``/onboarding/connections/{session_id}/`` (#571), which writes the sentinel
    after the user uploads a LinkedIn Connections.csv or explicitly skips. This
    route hops onward via the Gmail-config gate (#407), which preserves its
    own IMAP-test-before-handoff guarantee.
    """
    return RedirectResponse(f"/onboarding/gmail-config/{session_id}/", status_code=303)
