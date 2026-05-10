"""GET + POST /onboarding/gmail-config/{session_id} — Gmail-config gate (#407).

After the chat interview emits its config blocks (and the optional feed-config
gate runs), every flow passes through here. The user either saves an IMAP
credential pair and verifies it via the existing ``/config/gmail/test`` route,
or skips. Either way the flow then continues to the connections gate (#571),
which is the terminal step that writes the sentinel.

The IMAP-test-before-sentinel guarantee from #407 still holds — /finish here
rejects with a 400 until a successful test has run, and the connections gate
downstream only writes the sentinel after either an upload or an explicit skip.

Save/test mechanics are reused from :mod:`findajob.web.routes.gmail_config`
via HTMX — the existing ``_card.html`` partial is included unchanged in the
onboarding template.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from findajob import gmail_imap
from findajob.web import constants

router = APIRouter(prefix="/onboarding/gmail-config", tags=["onboarding"])


def _derive_status() -> str:
    config = gmail_imap.load_config()
    if config is None:
        return "off"
    state = gmail_imap.load_state()
    if state.last_error == "auth_failed":
        return "login_failed"
    if state.last_login_at:
        return "authorized"
    return "saved_untested"


def _ctx(request: Request, *, session_id: str, status: str, validation_error: str | None = None) -> dict:
    return {
        "session_id": session_id,
        "config": gmail_imap.load_config(),
        "state": gmail_imap.load_state(),
        "status": status,
        "validation_error": validation_error,
        "github_blob_url": constants.github_blob_url,
    }


@router.get("/{session_id}/", response_class=HTMLResponse)
def get_gmail_gate(session_id: str, request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding_gmail_config/index.html",
        context=_ctx(request, session_id=session_id, status=_derive_status()),
    )


@router.post("/{session_id}/skip")
def post_skip(session_id: str, request: Request) -> Response:
    """Skip Gmail setup; redirect to the connections gate.

    Skipping is always allowed — Gmail IMAP ingestion is optional. The user
    can come back later via ``/onboarding/?mode=rerun`` or directly at
    ``/config/gmail/``. The sentinel is not written here; the connections
    gate downstream writes it after upload or explicit skip.
    """
    return RedirectResponse(f"/onboarding/connections/{session_id}/", status_code=303)


@router.post("/{session_id}/finish", response_model=None)
def post_finish(session_id: str, request: Request) -> HTMLResponse | Response:
    """Verify Gmail config + test passed, then advance to the connections gate.

    The user must have (a) saved a credential pair and (b) run a successful
    IMAP test (``state.last_login_at`` is set and ``state.last_error`` is
    not ``auth_failed``). Otherwise re-render with a specific error.

    On success this redirects to the connections gate; the sentinel is not
    written here — that responsibility moved to the connections gate (#571).
    """
    templates = request.app.state.templates
    config = gmail_imap.load_config()
    if config is None:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_gmail_config/index.html",
            context=_ctx(
                request,
                session_id=session_id,
                status="off",
                validation_error=(
                    "Save your Gmail credentials and run Test connection "
                    "before continuing. If you don't want Gmail ingestion, "
                    "use Skip for now."
                ),
            ),
            status_code=400,
        )
    state = gmail_imap.load_state()
    if not state.last_login_at or state.last_error == "auth_failed":
        return templates.TemplateResponse(
            request=request,
            name="onboarding_gmail_config/index.html",
            context=_ctx(
                request,
                session_id=session_id,
                status=_derive_status(),
                validation_error=(
                    "Run Test connection successfully before continuing — "
                    "the IMAP credentials must verify against Gmail at least "
                    "once. If you'd rather come back to this later, use Skip "
                    "for now."
                ),
            ),
            status_code=400,
        )
    return RedirectResponse(f"/onboarding/connections/{session_id}/", status_code=303)
