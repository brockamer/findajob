# src/findajob/web/routes/admin_stacks.py
"""Operator-only multi-tenant stack health dashboard (#333).

Loaded only when FINDAJOB_OPERATOR_MODE=1. Reads cross-stack state from
/opt/stacks/findajob-*/state/ via read-only SQLite + bounded JSONL tail.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.admin.stack_discovery import discover_stacks
from findajob.admin.stack_health import StackHealth, gather

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_STACKS_ROOT = Path("/opt/stacks")


@router.get("/admin/stacks/", response_class=HTMLResponse)
def stacks_index(request: Request) -> HTMLResponse:
    """Render one row per active findajob stack."""
    t0 = time.perf_counter()
    stacks_root = Path(os.environ.get("FINDAJOB_ADMIN_STACKS_ROOT", str(DEFAULT_STACKS_ROOT)))
    stacks = discover_stacks(stacks_root)
    health = [gather(s) for s in stacks]
    sorted_health = _sort_operator_first(health)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info("admin_stacks: rendered N=%d stacks in %dms", len(sorted_health), elapsed_ms)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="admin/stacks_index.html",
        context={
            "health": sorted_health,
            "rendered_at": datetime.now(UTC),
            "stacks_root_display": str(stacks_root),
            "elapsed_ms": elapsed_ms,
        },
    )


def _sort_operator_first(rows: list[StackHealth]) -> list[StackHealth]:
    """When `FINDAJOB_OPERATOR_HANDLE` is set in the env, that handle's row
    renders first; the rest sort alphabetically. When unset, pure
    alphabetical.

    The handle is read from the env, never hardcoded — keeps tracked
    code free of operator-specific identifiers per CLAUDE.md PII /
    domain-neutrality rules.
    """
    op = os.environ.get("FINDAJOB_OPERATOR_HANDLE", "").strip()
    if not op:
        return sorted(rows, key=lambda r: r.handle)
    operator = [r for r in rows if r.handle == op]
    rest = sorted([r for r in rows if r.handle != op], key=lambda r: r.handle)
    return operator + rest
