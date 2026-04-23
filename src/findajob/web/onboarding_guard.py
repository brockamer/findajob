"""NUX guard dependency for the board/materials/stats routers (#148).

Redirects 307 → /onboarding/ when the sentinel is missing. Caches the
first True read on ``app.state.onboarding_complete`` so subsequent
requests skip the filesystem check until the inject handler resets it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from findajob.onboarding import is_complete


def require_onboarding_complete(request: Request) -> None:
    """Raise 307 to /onboarding/ if the stack is not yet configured.

    Attached via ``dependencies=[Depends(require_onboarding_complete)]`` on
    the board/materials/stats router includes.
    """
    cached = getattr(request.app.state, "onboarding_complete", None)
    if cached is True:
        return
    base_root: Path = request.app.state.base_root
    if is_complete(base_root):
        request.app.state.onboarding_complete = True
        return
    raise HTTPException(
        status_code=307,
        headers={"Location": "/onboarding/"},
    )
