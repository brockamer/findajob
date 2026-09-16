"""#841: /onboarding/restore/ — restore from a backup tarball as an alternative
to the chat-interview onboarding flow.

Reachable on factory-clean stacks (no sentinel) via a link on the onboarding
splash page. Upload a tarball, validate it, extract atomically, redirect to
the dashboard.

Already-onboarded stacks require an explicit confirm-overwrite step (mirrors
the #700 confirm-modal pattern).
"""

from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from findajob.onboarding import is_complete
from findajob.web.restore import MAX_UPLOAD_BYTES, RestoreResult, restore_from_tarball, validate_tarball

router = APIRouter(prefix="/onboarding/restore", tags=["onboarding"])


def _auth_active(request: Request) -> bool:
    """True when BasicAuthMiddleware is enforcing auth on every request."""
    return bool(getattr(request.app.state, "auth_user", "")) and bool(getattr(request.app.state, "auth_pass", ""))


@router.get("/", response_class=HTMLResponse)
def get_restore_page(request: Request) -> HTMLResponse:
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates
    already_onboarded = is_complete(base)
    return templates.TemplateResponse(
        request=request,
        name="onboarding/restore.html",
        context={
            "already_onboarded": already_onboarded,
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "needs_setup_token": not _auth_active(request),
        },
    )


@router.post("/upload", response_model=None)
async def post_restore(
    request: Request,
    backup_tarball: UploadFile,
    confirm_overwrite: str | None = Form(None),
    setup_token: str = Form(default=""),
) -> HTMLResponse | Response:
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates
    already_onboarded = is_complete(base)

    # Restore replaces data/, config/, candidate_context/, companies/ and
    # logs/ wholesale, so it is at least as sensitive as POST
    # /onboarding/auth and carries the same gate.  While the Basic-Auth
    # middleware is inactive (the bootstrap window, or a perimeter-only
    # deployment) the setup token is the only thing standing in front of
    # it.  Fail closed when no token was generated.
    if not _auth_active(request):
        expected_token = getattr(request.app.state, "setup_token", "")
        submitted = setup_token.strip()
        if not expected_token or not hmac.compare_digest(submitted, expected_token):
            return templates.TemplateResponse(
                request=request,
                name="onboarding/restore.html",
                context={
                    "already_onboarded": already_onboarded,
                    "needs_setup_token": True,
                    "validation_error": (
                        "Setup token is missing or incorrect. Find it in your "
                        "container logs (`fly logs --app findajob-<your-handle>` "
                        "on Fly, or `docker logs findajob-<stack>-scheduler-1` on "
                        "Docker) — search for FINDAJOB_SETUP_TOKEN."
                    ),
                    "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                },
                status_code=401,
            )

    if already_onboarded and confirm_overwrite != "yes":
        return templates.TemplateResponse(
            request=request,
            name="onboarding/restore.html",
            context={
                "already_onboarded": True,
                "needs_confirm": True,
                "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            },
            status_code=409,
        )

    raw = await backup_tarball.read(MAX_UPLOAD_BYTES + 1)

    error = validate_tarball(raw)
    if error is not None:
        return templates.TemplateResponse(
            request=request,
            name="onboarding/restore.html",
            context={
                "already_onboarded": already_onboarded,
                "validation_error": error,
                "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            },
            status_code=400,
        )

    result: RestoreResult = restore_from_tarball(raw, base)

    if not result.success:
        return templates.TemplateResponse(
            request=request,
            name="onboarding/restore.html",
            context={
                "already_onboarded": already_onboarded,
                "restore_error": result.error,
                "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            },
            status_code=500,
        )

    if hasattr(request.app.state, "onboarding_complete"):
        request.app.state.onboarding_complete = True

    return RedirectResponse("/board/dashboard", status_code=303)
