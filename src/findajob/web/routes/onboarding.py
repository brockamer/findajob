"""Onboarding NUX: landing page + per-stack API-key collection.

The flow has two steps that share the ``onboarding_sessions`` table:

- ``POST /onboarding/keys`` collects the tester's OpenRouter / RapidAPI
  credentials and persists them on a credentials-only session row.
- ``POST /onboarding/interview/start`` (lives in
  :mod:`findajob.web.routes.onboarding_interview`) promotes that row into
  an active interview session.

The earlier paste-back path (run the interview in another LLM, paste the
emission back here) was removed 2026-05-02 — see CHANGELOG.
"""

from __future__ import annotations

import os
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
from findajob.onboarding.openrouter_smoke import verify_openrouter_key
from findajob.onboarding.rapidapi_smoke import verify_rapidapi_key
from findajob.onboarding.session_store import (
    Credentials,
    Session,
    create_session,
    find_active,
    find_credentials_only,
    get_credentials,
    has_any_credentials,
    set_credentials,
)

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


def _has_in_app_interview_capability(request: Request) -> bool:
    """True iff any session row has a tester OpenRouter key set.

    Step 1 (API-key collection at ``/onboarding/keys``) is the single
    gate for the in-app interview — without it, finalize has no key to
    verify and the smoke check strands the user on an unfinishable
    session.

    Uses :func:`has_any_credentials` (not :func:`find_credentials_only`)
    so the gate stays True once the interview starts and the credentials
    bind to the active session row — otherwise the resume affordance
    would disappear mid-flow.
    """
    db_path: Path | None = getattr(request.app.state, "db_path", None)
    if db_path is None:
        return False
    try:
        conn = connect(db_path, timeout=5)
    except sqlite3.Error:
        return False
    try:
        return has_any_credentials(conn)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _active_session_for_index(request: Request) -> Session | None:
    """Look up a resumable in-app interview session for the index page.

    Returns ``None`` when:
    - In-app interview is unavailable on this stack (no tester
      credentials collected at /onboarding/ Step 1)
    - DB unavailable or schema doesn't include ``onboarding_sessions``
    - no recent un-completed session exists

    Failures are silent — the resume affordance is a convenience, not a
    correctness requirement, and failing the index render over a session
    lookup glitch would break the whole onboarding entry point.
    """
    if not _has_in_app_interview_capability(request):
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
        # A credentials-only session row (created by POST /onboarding/keys)
        # satisfies find_active's filter (no completed_at, recent last_turn_at)
        # but has history=[]. The resume banner should only fire when the user
        # has actually started chatting.
        if active is not None and not active.history:
            active = None
        return active
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _credentials_for_index(request: Request) -> Credentials | None:
    """Look up the credentials-only session's keys, or ``None`` if no row.

    Returns ``None`` on any failure path (DB missing, schema older than
    #339) so the index render can degrade gracefully. The Step 2
    affordance is gated on ``_has_in_app_interview_capability`` which
    handles its own DB-failure path; callers don't need to differentiate
    "no credentials row" from "DB unreachable."
    """
    db_path: Path | None = getattr(request.app.state, "db_path", None)
    if db_path is None:
        return None
    try:
        conn = connect(db_path, timeout=5)
    except sqlite3.Error:
        return None
    try:
        sess = find_credentials_only(conn)
        if sess is None:
            return None
        return get_credentials(conn, sess.id)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _last4(value: str | None) -> str:
    """Render the last 4 chars of a key for masked display, '' on None."""
    if not value:
        return ""
    return value[-4:]


def _env_var_keys() -> tuple[str | None, str | None]:
    """Read OPENROUTER_API_KEY / RAPIDAPI_KEY from the process environment.

    Returns ``(openrouter, rapidapi)``. Empty / whitespace-only values are
    normalized to ``None`` so the caller can boolean-check.

    Covers both delivery paths transparently:
    - **Fly.io**: ``fly secrets set`` materializes secrets into ``os.environ``.
    - **Docker Compose**: ``env_file: ./state/data/.env`` (see
      ``ops/compose.yaml.example``) loads data/.env values into ``os.environ``
      at container startup. (Caveat: ``docker compose restart`` does NOT
      re-read env_file — only ``up -d`` does — but that's an existing
      operational fact, not in scope for env-var detection.)

    The literal AC #1 reading ("os.environ AND in data/.env") is satisfied by
    reading os.environ alone, since data/.env's contents become env vars at
    container startup on every supported deploy.
    """
    openrouter = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    rapidapi = (os.environ.get("RAPIDAPI_KEY") or "").strip()
    return (openrouter or None, rapidapi or None)


@router.get("/onboarding/", response_class=HTMLResponse)
def onboarding_index(
    request: Request,
    mode: str = "",
    manual: str = "",
) -> HTMLResponse:
    """Landing page. ``mode=rerun`` flips on the backup warning.

    When the stack is already onboarded (sentinel file present) AND no
    Step 1 credentials have been collected yet AND the user is not in
    rerun mode, surface a brief "you've already onboarded" hint so an
    already-configured tester who lands here from a stale link or out
    of curiosity doesn't think findajob has forgotten them.

    ``manual=1`` suppresses env-var detection for that render. Used by
    the Override affordance on the "detected keys" UI and by the reset
    path's redirect so an operator who clicks "Change keys" lands on
    the empty form rather than the detected UI they just opted out of.
    The choice is per-render only — closing the tab forgets it. This
    mirrors how every other form behaves and avoids new schema state.
    """
    templates = request.app.state.templates
    active = _active_session_for_index(request)
    creds = _credentials_for_index(request)
    keys_collected = creds is not None and (creds.openrouter_api_key is not None)

    env_openrouter, env_rapidapi = (None, None) if manual == "1" else _env_var_keys()
    env_keys_available = env_openrouter is not None and not keys_collected

    base_root: Path = request.app.state.base_root
    is_already_onboarded = (base_root / "data" / ".onboarding-complete").is_file()
    show_already_onboarded_hint = is_already_onboarded and not keys_collected and mode != "rerun"

    return templates.TemplateResponse(
        request=request,
        name="onboarding/index.html",
        context={
            "is_rerun": mode == "rerun",
            "active_session_id": active.id if active else None,
            "active_session_age": _humanize_minutes_ago(active.last_turn_at) if active else None,
            "keys_collected": keys_collected,
            "openrouter_last4": _last4(creds.openrouter_api_key) if creds else "",
            "rapidapi_last4": _last4(creds.rapidapi_key) if creds else "",
            "keys_error": None,
            "rapidapi_input": "",
            "show_already_onboarded_hint": show_already_onboarded_hint,
            "env_keys_available": env_keys_available,
            "env_openrouter_last4": _last4(env_openrouter),
            "env_rapidapi_last4": _last4(env_rapidapi),
        },
    )


def _render_keys_error(
    request: Request,
    *,
    error: str,
    rapidapi_input: str = "",
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
            "keys_error": error,
            "rapidapi_input": rapidapi_input,
            "show_already_onboarded_hint": False,
            # The error path is showing the manual form by definition, so
            # suppress env-var detection here regardless. Otherwise a failed
            # smoke check would flip the user back to the detected UI mid-flow.
            "env_keys_available": False,
            "env_openrouter_last4": "",
            "env_rapidapi_last4": "",
        },
        status_code=400,
    )


@router.post("/onboarding/keys", response_model=None)
def onboarding_keys(
    request: Request,
    openrouter_api_key: str = Form(default=""),
    rapidapi_key: str = Form(default=""),
    reset: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Step 1 of #339: collect two API keys; persist to the credentials session.

    Idempotent on retry: ``UPDATE``s the existing credentials-only session
    when present rather than creating a new one. Format / smoke-check
    failures DO NOT write to the DB — the user re-renders Step 1 with a
    preserved-input form, and the credentials row (if any) is unchanged.

    Reset path: a POST with ``reset=1`` clears the existing credentials and
    sends the user back to a blank Step 1. The chat session, if any, is
    intentionally left intact — the next chat turn will pick up whatever
    OpenRouter key the user re-supplies via Step 1.
    """
    db_path: Path = request.app.state.db_path
    conn = connect(db_path, timeout=5)
    try:
        if reset == "1":
            existing = find_credentials_only(conn)
            if existing is not None:
                set_credentials(
                    conn,
                    existing.id,
                    openrouter_api_key="",
                    rapidapi_key="",
                )
            # Redirect with ?manual=1 so the operator who clicked "Change
            # keys" lands on the empty form, not the detected-keys UI from
            # env vars they implicitly opted out of by clicking reset.
            return RedirectResponse(url="/onboarding/?manual=1", status_code=303)

        ok, err = validate_openrouter_format(openrouter_api_key)
        if not ok:
            return _render_keys_error(
                request,
                error=err,
                rapidapi_input=rapidapi_key,
            )
        ok, err = validate_rapidapi_format(rapidapi_key)
        if not ok:
            return _render_keys_error(
                request,
                error=err,
                rapidapi_input=rapidapi_key,
            )

        smoke_ok, smoke_err = verify_openrouter_key(openrouter_api_key.strip())
        if not smoke_ok:
            return _render_keys_error(
                request,
                error=(
                    "OpenRouter rejected the key when we tried to verify it. "
                    f"{smoke_err or ''} Fix the key and click Save again."
                ).strip(),
                rapidapi_input=rapidapi_key,
            )

        # RapidAPI is an optional field — smoke only when a value was supplied.
        # Step 1 runs before the candidate's target_locations.txt exists, so
        # the adapter's live_test path isn't usable here; the smoke module
        # makes a stdlib auth probe instead (#689).
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
                )

        # All validations passed — UPDATE existing credentials session, or
        # INSERT a fresh one. This prevents orphan-row accumulation when a
        # user paste-typos several times before getting it right.
        existing = find_credentials_only(conn)
        if existing is not None:
            session_id = existing.id
        else:
            session_id = create_session(conn)
        # Invariant (#689 AC #4): set_credentials is the *only* write path
        # that mutates tester_rapidapi_key after this point. The interview's
        # opt-out path must NOT call set_credentials() with a blank
        # rapidapi_key — Step-1 keys are preserved through opt-out so the
        # finalize injector can decide whether to emit them based on the
        # interview's source-selection, not on whether the column is NULL.
        set_credentials(
            conn,
            session_id,
            openrouter_api_key=openrouter_api_key.strip(),
            rapidapi_key=rapidapi_key.strip(),
        )
        return RedirectResponse(url="/onboarding/", status_code=303)
    finally:
        conn.close()


@router.post("/onboarding/keys/use-detected", response_model=None)
def onboarding_keys_use_detected(request: Request) -> HTMLResponse | RedirectResponse:
    """Step 1, alternative entry: persist keys already in the container env.

    Reads ``OPENROUTER_API_KEY`` and ``RAPIDAPI_KEY`` from ``os.environ``
    server-side — the browser never sees the values, so no hidden inputs
    carry secrets across the wire. Runs the same format + smoke +
    persistence chain as :func:`onboarding_keys`, with error messages
    tailored to the container-env source so a stale Fly secret or a
    typo'd compose ``env_file`` value tells the user where to look.

    If no OpenRouter key is present in the env (race against Fly secret
    propagation, or the operator hit the route directly without env vars
    set), redirect to ``/onboarding/?manual=1`` so the user lands on the
    empty form rather than a confusing error page.
    """
    env_openrouter, env_rapidapi = _env_var_keys()

    if env_openrouter is None:
        return RedirectResponse(url="/onboarding/?manual=1", status_code=303)

    rapidapi_value = env_rapidapi or ""

    ok, err = validate_openrouter_format(env_openrouter)
    if not ok:
        return _render_keys_error(
            request,
            error=(
                f"The OpenRouter key in this container's environment failed format "
                f"validation: {err} Check the value of OPENROUTER_API_KEY "
                "(Fly secret or data/.env) and re-deploy, or enter a key manually below."
            ),
        )
    ok, err = validate_rapidapi_format(rapidapi_value)
    if not ok:
        return _render_keys_error(
            request,
            error=(
                f"The RapidAPI key in this container's environment failed format "
                f"validation: {err} Check the value of RAPIDAPI_KEY (Fly secret "
                "or data/.env) and re-deploy, or enter keys manually below."
            ),
        )

    smoke_ok, smoke_err = verify_openrouter_key(env_openrouter)
    if not smoke_ok:
        return _render_keys_error(
            request,
            error=(
                "OpenRouter rejected the key from this container's environment. "
                f"{smoke_err or ''} The OPENROUTER_API_KEY may be stale or revoked. "
                "Rotate it (Fly secret or data/.env) and re-deploy, or enter a "
                "fresh key manually below."
            ).strip(),
        )

    if rapidapi_value:
        rapid_ok, rapid_err = verify_rapidapi_key(rapidapi_value)
        if not rapid_ok:
            return _render_keys_error(
                request,
                error=(
                    "RapidAPI rejected the key from this container's environment. "
                    f"{rapid_err or ''} The RAPIDAPI_KEY may be stale or revoked. "
                    "Rotate it (Fly secret or data/.env) and re-deploy, or enter "
                    "keys manually below."
                ).strip(),
            )

    db_path: Path = request.app.state.db_path
    conn = connect(db_path, timeout=5)
    try:
        existing = find_credentials_only(conn)
        if existing is not None:
            session_id = existing.id
        else:
            session_id = create_session(conn)
        set_credentials(
            conn,
            session_id,
            openrouter_api_key=env_openrouter,
            rapidapi_key=rapidapi_value,
        )
        return RedirectResponse(url="/onboarding/", status_code=303)
    finally:
        conn.close()
