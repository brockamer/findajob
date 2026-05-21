"""Shared cron-launch handler used by both `/tools/trigger-cron/{slug}` (#650)
and the existing `/board/trigger-triage` banner route (#752).

Single launch code path: registry lookup → enabled check → concurrency
check → spend-ceiling check (if gated) → detached subprocess.Popen →
audit event → 303 redirect. Callers pass an optional `redirect_url`
override so the banner can preserve its existing destination.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from findajob.audit import log_event
from findajob.paths import BASE
from findajob.spend_ceiling import check_launch_gate
from findajob.web.cron_registry import (
    CRON_TILES_BY_SLUG,
    is_currently_running,
)


def dispatch_cron(
    slug: str,
    db: sqlite3.Connection,
    base_root: Path,
    *,
    source: str = "tools_panel",
    redirect_url: str | None = None,
) -> RedirectResponse:
    """Launch a cron by slug. See spec §4.4."""
    tile = CRON_TILES_BY_SLUG.get(slug)
    if tile is None:
        raise HTTPException(status_code=404, detail=f"Unknown cron slug: {slug}")
    if not tile.enabled:
        raise HTTPException(
            status_code=409,
            detail=f"Cron '{slug}' is disabled in scheduled-jobs.yaml",
        )
    if is_currently_running(slug, base_root):
        raise HTTPException(
            status_code=409,
            detail=f"'{slug}' is already running — retry after current run finishes",
        )
    if tile.gated_by_spend_ceiling:
        refusal = check_launch_gate(db)
        if refusal is not None:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly LLM spend ceiling reached: "
                    f"${refusal.current_sum_usd:.2f} / ${refusal.ceiling_usd:.2f}. "
                    f"Raise or disable the ceiling in /settings/."
                ),
            )

    # Per T3 reviewer follow-up: CronTile has separate script_path + args (tuple).
    # No string-splitting needed.
    argv = [sys.executable, f"{BASE}/{tile.script_path}", *tile.args]
    # Race-close: pre-emit cron_started so a follow-up POST's is_currently_running
    # gate sees the run BEFORE the spawned child reaches its own cron_event_span
    # emission (~100ms later). The duplicate cron_started from the child is
    # harmless — is_currently_running only checks the newest event per slug.
    log_event("cron_started", cron=slug, source=source)
    try:
        subprocess.Popen(argv, start_new_session=True, env={**os.environ})
    except Exception as exc:
        # Race-close pre-emit (above) wrote cron_started; pair it with
        # cron_finished status=failed so is_currently_running sees the slug
        # as releasable. Without this, a dangling cron_started would brick
        # the slug for max_runtime_minutes (120min triage, 15min watchdog,
        # 10min discover/detect-rejections).
        log_event("cron_finished", cron=slug, status="failed")
        log_event("web_cron_dispatch_failed", cron=slug, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to launch cron '{slug}': {exc}") from exc

    log_event("web_cron_dispatched", cron=slug, source=source)
    return RedirectResponse(
        url=redirect_url or f"/tools/?triggered={slug}",
        status_code=303,
    )
