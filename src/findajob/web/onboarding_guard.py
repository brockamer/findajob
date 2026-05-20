"""NUX guard dependency for the board/materials/stats routers (#148).

Redirects 307 → /onboarding/ when the sentinel is missing. Caches the
first True read on ``app.state.onboarding_complete`` so subsequent
requests skip the filesystem check until the inject handler resets it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from findajob.onboarding import is_complete


def onboarding_complete(request: Request) -> bool:
    """Predicate form of the guard — True iff the sentinel exists.

    Exposed as a Jinja global so templates can gate nav widgets that would
    otherwise poll guarded endpoints and HTMX-swap the resulting 307 →
    /onboarding/ body into themselves (#440 / fresh-install loop).
    """
    cached = getattr(request.app.state, "onboarding_complete", None)
    if cached is True:
        return True
    base_root: Path = request.app.state.base_root
    if is_complete(base_root):
        request.app.state.onboarding_complete = True
        return True
    return False


def require_onboarding_complete(request: Request) -> None:
    """Raise 307 to /onboarding/ if the stack is not yet configured.

    Attached via ``dependencies=[Depends(require_onboarding_complete)]`` on
    the board/materials/stats router includes.

    HTMX-aware branch (#619): when the request is HTMX-initiated, respond
    with ``200 + HX-Redirect: /onboarding/`` instead of a 30x. HTMX honors
    `HX-Redirect` by setting `window.location` BEFORE any element swap, so
    the body is never rendered into the trigger element — sidestepping the
    redirect-loop bug class that #618 fixed at the template layer for the
    bell widget specifically. This is the boundary-enforced defense so that
    any future nav widget polling a guarded endpoint is safe by default.
    """
    if onboarding_complete(request):
        return
    if request.headers.get("HX-Request"):
        raise HTTPException(
            status_code=200,
            headers={"HX-Redirect": "/onboarding/"},
        )
    raise HTTPException(
        status_code=307,
        headers={"Location": "/onboarding/"},
    )
