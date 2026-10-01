"""Onboarding NUX: landing page + per-stack API-key collection.

The flow has two steps:

- ``POST /onboarding/keys`` collects the user's OpenRouter / RapidAPI /
  Gemini keys and saves them to ``data/.env`` and the process environment.
- ``POST /onboarding/interview/start`` (lives in
  :mod:`findajob.web.routes.onboarding_interview`) starts an interview
  session in the ``onboarding_sessions`` table.

The earlier paste-back path (run the interview in another LLM, paste the
emission back here) was removed 2026-05-02 — see CHANGELOG.
"""

from __future__ import annotations

import hmac
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.db import connect
from findajob.onboarding.key_validation import (
    validate_openrouter_format,
    validate_rapidapi_format,
)
from findajob.onboarding.keys import current_keys, save_keys
from findajob.onboarding.openrouter_smoke import verify_openrouter_key
from findajob.onboarding.rapidapi_smoke import verify_rapidapi_key
from findajob.onboarding.session_store import (
    Session,
    find_active,
)
from findajob.web.auth_env import is_auth_configured, write_auth_credentials

router = APIRouter()


def _humanize_minutes_ago(iso_utc: str) -> str:
    """Render a friendly "X minutes ago" / "X hours ago" string for the
    resume affordance (#336 Task 8). Input is the session's ``last_turn_at``
    value, written by session_store as ``YYYY-MM-DDTHH:MM:SSZ``.

    Tolerates parse failures by returning a generic "earlier today" — the
    affordance still works, it just loses precision.
    """
    try:
        last = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return "earlier today"
    delta = datetime.now(UTC) - last
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def _has_in_app_interview_capability() -> bool:
    """True iff an OpenRouter key is saved for this instance.

    Step 1 is the gate for the in-app interview: without a key the chat
    cannot run and finalize has nothing to verify. The key lives in
    ``data/.env`` and the process environment, never in the database.
    """
    return bool(current_keys()[0])


def _active_session_for_index(request: Request) -> Session | None:
    """Look up a resumable in-app interview session for the index page.

    Returns ``None`` when:
    - In-app interview is unavailable on this stack (no user
      credentials collected at /onboarding/ Step 1)
    - DB unavailable or schema doesn't include ``onboarding_sessions``
    - no recent un-completed session exists

    Failures are silent — the resume affordance is a convenience, not a
    correctness requirement, and failing the index render over a session
    lookup glitch would break the whole onboarding entry point.
    """
    if not _has_in_app_interview_capability():
        return None
    db_path: Path | None = getattr(request.app.state, "db_path", None)
    if db_path is None:
        return None
    try:
        conn = connect(db_path, timeout=5)
    except sqlite3.Error:
        return None
    try:
        active = find_active(conn)
        # A session row the user created by clicking Start
        # (/onboarding/interview/start) but never chatted in satisfies
        # find_active's filter (no completed_at, recent last_turn_at) but has
        # history=[]. The resume banner should only fire when the user has
        # actually started chatting.
        if active is not None and not active.history:
            active = None
        return active
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _last4(value: str | None) -> str:
    """Render the last 4 chars of a key for masked display, '' on None."""
    if not value:
        return ""
    return value[-4:]


@router.get("/onboarding/", response_class=HTMLResponse)
def onboarding_index(
    request: Request,
    mode: str = "",
    manual: str = "",
    auth_error: str = "",
    auth_username_input: str = "",
) -> HTMLResponse:
    """Landing page. ``mode=rerun`` flips on the backup warning.

    When the stack is already onboarded (sentinel file present) AND no
    OpenRouter key is saved AND the user is not in
    rerun mode, surface a brief "you've already onboarded" hint so an
    already-configured user who lands here from a stale link or out
    of curiosity doesn't think findajob has forgotten them.

    ``manual=1`` shows the empty form even when keys are saved. Used by the
    "Change keys" link. The choice is per-render only; the saved keys stay
    until new ones are saved.
    """
    templates = request.app.state.templates
    active = _active_session_for_index(request)
    openrouter, rapidapi, gemini = current_keys()
    # ``manual=1`` (the "Change keys" link) shows the empty form even when keys are saved.
    keys_collected = bool(openrouter) and manual != "1"

    base_root: Path = request.app.state.base_root
    is_already_onboarded = (base_root / "data" / ".onboarding-complete").is_file()
    show_already_onboarded_hint = is_already_onboarded and not openrouter and mode != "rerun"

    return templates.TemplateResponse(
        request=request,
        name="onboarding/index.html",
        context={
            "is_rerun": mode == "rerun",
            "active_session_id": active.id if active else None,
            "active_session_age": _humanize_minutes_ago(active.last_turn_at) if active else None,
            "keys_collected": keys_collected,
            "openrouter_last4": _last4(openrouter) if keys_collected else "",
            "rapidapi_last4": _last4(rapidapi) if keys_collected else "",
            "gemini_last4": _last4(gemini) if keys_collected else "",
            "keys_error": None,
            "rapidapi_input": "",
            "gemini_input": "",
            "show_already_onboarded_hint": show_already_onboarded_hint,
            "auth_configured": is_auth_configured(base_root),
            "auth_error": auth_error or None,
            "auth_username_input": auth_username_input,
            "setup_token_required": bool(getattr(request.app.state, "setup_token", "")),
        },
    )


@router.post("/onboarding/auth", response_model=None)
def onboarding_auth(
    request: Request,
    auth_username: str = Form(default=""),
    auth_password: str = Form(default=""),
    auth_password_confirm: str = Form(default=""),
    setup_token: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Step 0 of #895: set instance auth credentials during onboarding.

    Writes ``FINDAJOB_AUTH_USER`` / ``FINDAJOB_AUTH_PASS`` to three targets:
    ``app.state`` (immediate middleware effect), ``os.environ`` (in-process),
    ``data/.env`` (survives restarts).  After redirect, the middleware
    enforces auth and the browser prompts the user to log in.

    Drive-by squat defense: requires the one-time setup token written to
    container stdout at boot.  Without log access (``fly logs`` / ``docker
    logs``) an attacker who hits a freshly-deployed unprotected instance
    can't lock out the legitimate operator.
    """
    # Token gate applies whenever the middleware isn't already enforcing
    # auth.  Failing closed on empty `expected_token` plugs the partial-
    # config drive-by hole — without this, a typo'd compose.yaml that
    # leaves the middleware fail-open AND skipped the token-generation
    # branch would let any caller POST credentials with no token at all.
    auth_active = bool(getattr(request.app.state, "auth_user", "")) and bool(
        getattr(request.app.state, "auth_pass", "")
    )
    if not auth_active:
        expected_token = getattr(request.app.state, "setup_token", "")
        submitted_token = setup_token.strip()
        if not expected_token or not hmac.compare_digest(submitted_token, expected_token):
            return _render_auth_error(
                request,
                "Setup token is missing or incorrect. Find it in your container logs "
                "(`fly logs --app findajob-<your-handle>` on Fly, or `docker logs "
                "findajob-<stack>-scheduler-1` on Docker) — search for FINDAJOB_SETUP_TOKEN.",
                auth_username.strip(),
            )

    username = auth_username.strip()
    password = auth_password.strip()
    confirm = auth_password_confirm.strip()

    if not username:
        return _render_auth_error(request, "Username is required.", username)
    if not password:
        return _render_auth_error(request, "Password is required.", username)
    if len(password) < 8:
        return _render_auth_error(request, "Password must be at least 8 characters.", username)
    if password != confirm:
        return _render_auth_error(request, "Passwords do not match.", username)

    base_root: Path = request.app.state.base_root
    try:
        write_auth_credentials(base_root, username, password)
    except ValueError:
        return _render_auth_error(request, "Username and password cannot contain line breaks.", "")

    request.app.state.auth_user = username
    request.app.state.auth_pass = password
    request.app.state.setup_token = ""

    return RedirectResponse(url="/onboarding/", status_code=303)


def _render_auth_error(
    request: Request,
    error: str,
    username_input: str,
) -> HTMLResponse:
    """Re-render the onboarding index with an auth-step error (400)."""
    response = onboarding_index(
        request,
        auth_error=error,
        auth_username_input=username_input,
    )
    response.status_code = 400
    return response


def _render_keys_error(
    request: Request,
    *,
    error: str,
    rapidapi_input: str = "",
    gemini_input: str = "",
) -> HTMLResponse:
    """Re-render the index page with a Step 1 error, preserving optional inputs.

    OpenRouter input is intentionally NOT preserved — when verification fails
    the user typically re-pastes from the provider's key page rather than
    correcting in place, and reflowing a password-class field across requests
    invites confusion. RapidAPI is preserved because the user may only need
    to fix the OpenRouter key.
    """
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/index.html",
        context={
            "is_rerun": False,
            "active_session_id": None,
            "active_session_age": None,
            "keys_collected": False,
            "openrouter_last4": "",
            "rapidapi_last4": "",
            "gemini_last4": "",
            "keys_error": error,
            "rapidapi_input": rapidapi_input,
            "gemini_input": gemini_input,
            "show_already_onboarded_hint": False,
            "auth_configured": is_auth_configured(request.app.state.base_root),
            "auth_error": None,
            "auth_username_input": "",
            "setup_token_required": bool(getattr(request.app.state, "setup_token", "")),
        },
        status_code=400,
    )


@router.post("/onboarding/keys", response_model=None)
def onboarding_keys(
    request: Request,
    openrouter_api_key: str = Form(default=""),
    rapidapi_key: str = Form(default=""),
    gemini_api_key: str = Form(default=""),
    reset: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Step 1: validate the API keys, then save them to ``data/.env`` and the environment.

    Format and live smoke-check failures save nothing; the form re-renders
    with the RapidAPI and Gemini inputs preserved. Nothing is written to the
    database. ``reset=1`` ("Change keys") only shows the empty form again:
    the saved keys stay until new ones are saved, so the pipeline keeps
    running.
    """
    if reset == "1":
        return RedirectResponse(url="/onboarding/?manual=1", status_code=303)

    ok, err = validate_openrouter_format(openrouter_api_key)
    if not ok:
        return _render_keys_error(request, error=err, rapidapi_input=rapidapi_key, gemini_input=gemini_api_key)
    ok, err = validate_rapidapi_format(rapidapi_key)
    if not ok:
        return _render_keys_error(request, error=err, rapidapi_input=rapidapi_key, gemini_input=gemini_api_key)

    smoke_ok, smoke_err = verify_openrouter_key(openrouter_api_key.strip())
    if not smoke_ok:
        return _render_keys_error(
            request,
            error=(
                "OpenRouter rejected the key when we tried to verify it. "
                f"{smoke_err or ''} Fix the key and click Save again."
            ).strip(),
            rapidapi_input=rapidapi_key,
            gemini_input=gemini_api_key,
        )

    # RapidAPI is optional — smoke only when a value was supplied. Step 1 runs
    # before target_locations.txt exists, so the smoke module makes a stdlib
    # auth probe instead of the adapter's live_test path (#689).
    if rapidapi_key.strip():
        rapid_ok, rapid_err = verify_rapidapi_key(rapidapi_key.strip())
        if not rapid_ok:
            return _render_keys_error(
                request,
                error=(
                    "RapidAPI rejected the key when we tried to verify it. "
                    f"{rapid_err or ''} Fix the key and click Save again."
                ).strip(),
                rapidapi_input=rapidapi_key,
                gemini_input=gemini_api_key,
            )

    try:
        save_keys(
            request.app.state.base_root,
            openrouter_api_key=openrouter_api_key,
            rapidapi_key=rapidapi_key,
            gemini_api_key=gemini_api_key,
        )
    except ValueError:
        # The Gemini field has no format check; a value with a line break or
        # NUL cannot be stored as one env line.
        return _render_keys_error(
            request,
            error="A key contains a line break or a control character. Paste it again and click Save.",
            rapidapi_input=rapidapi_key,
        )
    return RedirectResponse(url="/onboarding/", status_code=303)
