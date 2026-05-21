"""Write-side handlers for the `/tools/` operator console (#650).

`/tools/` GET (in tools.py) is read-only and renders trigger tiles +
links to the log viewer. Each trigger button posts to this router's
`/tools/trigger-cron/{slug}` handler, which delegates to the shared
`dispatch_cron` helper. Following the board.py / board_actions.py
split convention (#150 precedent).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from findajob.web.cron_dispatch import dispatch_cron
from findajob.web.routes.materials import get_db

router = APIRouter()


@router.post("/tools/trigger-cron/{slug}", response_model=None)
def trigger_cron(
    slug: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Manually fire a registered cron. See `cron_registry.CRON_TILES`
    for the registered slugs and spec §4.4 for the dispatch contract.
    """
    base_root = request.app.state.base_root
    return dispatch_cron(slug, db, base_root, source="tools_panel")
