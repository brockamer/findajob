"""`/tools/` — guided prompts (#150) + manual cron triggers (#650).

Renders two distinct panels:
1. Legacy prompt/link tiles from `tools_registry.TILES` (Phase 1).
2. Manual-trigger tiles from `cron_registry.CRON_TILES` (#650),
   each with live "running" / "last run" state read from pipeline.jsonl.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from findajob.web.cron_registry import (
    CRON_TILES,
    is_currently_running,
    last_run_at,
)
from findajob.web.routes.materials import get_db
from findajob.web.tools_registry import hydrate_tiles

router = APIRouter()


def _build_trigger_panel(base_root: Path, db: sqlite3.Connection) -> list[dict]:
    """One dict per CronTile, augmented with live state for the template."""
    out: list[dict] = []
    for tile in CRON_TILES:
        cost_label = tile.cost_estimate_fn(db) if tile.cost_estimate_fn else None
        out.append(
            {
                "slug": tile.slug,
                "label": tile.label,
                "description": tile.description,
                "enabled": tile.enabled,
                "confirm_required": tile.confirm_required,
                "cost_label": cost_label,
                "running": is_currently_running(tile.slug, base_root),
                "last_run": last_run_at(tile.slug, base_root),
            }
        )
    return out


@router.get("/tools/", response_class=HTMLResponse)
def tools_index(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
    triggered: str = "",
) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="tools/index.html",
        context={
            "tiles": hydrate_tiles(base_root),
            "triggers": _build_trigger_panel(base_root, db),
            "triggered_slug": triggered,
        },
    )
