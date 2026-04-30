"""Routes for /config/gmail/{,save,test,disconnect}.

The disclosure banner rendered on this page is the single source of truth
for findajob's user-facing Gmail-access claims. See
docs/superpowers/specs/2026-04-30-330-design.md §4 for the full transparency
contract.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from findajob import gmail_imap
from findajob.web import constants

router = APIRouter()


def _ctx(request: Request, *, status: str, validation_error: str | None = None) -> dict:
    return {
        "config": gmail_imap.load_config(),
        "state": gmail_imap.load_state(),
        "status": status,
        "validation_error": validation_error,
        "github_blob_url": constants.github_blob_url,
    }


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


@router.get("/config/gmail/", response_class=HTMLResponse)
def get_gmail_config(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="gmail_config/index.html",
        context=_ctx(request, status=_derive_status()),
    )


def _validate(address: str, app_password: str, sender_allowlist: str) -> str | None:
    if "@" not in address or len(address) > 254:
        return "Enter a valid email address."
    pw_stripped = app_password.replace(" ", "")
    if len(pw_stripped) != 16 or not pw_stripped.isalnum():
        return "App password must be 16 characters. Generate one at myaccount.google.com/apppasswords."
    senders = [line.strip() for line in sender_allowlist.splitlines() if line.strip()]
    if not senders or len(senders) > 20:
        return "Each sender must be a valid email address. Max 20."
    if not all("@" in s for s in senders):
        return "Each sender must be a valid email address. Max 20."
    return None


@router.post("/config/gmail/save", response_class=HTMLResponse)
def save_gmail_config(
    request: Request,
    address: str = Form(...),
    app_password: str = Form(...),
    sender_allowlist: str = Form(...),
) -> HTMLResponse:
    templates = request.app.state.templates
    err = _validate(address, app_password, sender_allowlist)
    if err:
        return templates.TemplateResponse(
            request=request,
            name="gmail_config/_card.html",
            context=_ctx(request, status=_derive_status(), validation_error=err),
        )
    senders = [line.strip() for line in sender_allowlist.splitlines() if line.strip()]
    cfg = gmail_imap.GmailConfig(
        address=address,
        app_password=app_password.replace(" ", ""),
        sender_allowlist=senders,
        configured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    gmail_imap.save_config(cfg)
    return templates.TemplateResponse(
        request=request,
        name="gmail_config/_card.html",
        context=_ctx(request, status="saved_untested"),
    )


@router.post("/config/gmail/test", response_class=HTMLResponse)
def test_gmail_config(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    cfg = gmail_imap.load_config()
    if cfg is None:
        return templates.TemplateResponse(
            request=request,
            name="gmail_config/_card.html",
            context=_ctx(
                request,
                status="off",
                validation_error="Save credentials before testing.",
            ),
        )
    result = gmail_imap.test_login(cfg)
    if result == gmail_imap.TestResult.SUCCESS:
        state = gmail_imap.load_state()
        gmail_imap.save_state(
            replace(
                state,
                last_login_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                last_error=None,
            )
        )
        return templates.TemplateResponse(
            request=request,
            name="gmail_config/_card.html",
            context=_ctx(request, status="authorized"),
        )
    if result == gmail_imap.TestResult.AUTH_FAILED:
        state = gmail_imap.load_state()
        gmail_imap.save_state(replace(state, last_error="auth_failed"))
        return templates.TemplateResponse(
            request=request,
            name="gmail_config/_card.html",
            context=_ctx(request, status="login_failed"),
        )
    return templates.TemplateResponse(
        request=request,
        name="gmail_config/_card.html",
        context=_ctx(request, status="connection_error"),
    )


@router.post("/config/gmail/disconnect", response_class=HTMLResponse)
def disconnect_gmail_config(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    for path in (gmail_imap.GMAIL_CONFIG_PATH, gmail_imap.GMAIL_STATE_PATH):
        p = Path(path)
        if p.exists():
            p.unlink()
    return templates.TemplateResponse(
        request=request,
        name="gmail_config/_card.html",
        context=_ctx(request, status="off"),
    )
