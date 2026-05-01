"""Onboarding NUX: landing page + prompt endpoint + paste-back inject (#148).

#339 added the keys-collection layer: ``POST /onboarding/keys`` collects
the tester's own OpenRouter / RapidAPI / Google credentials before either
interview path enables. The credentials live in a session row created (or
updated, on retry) by that handler and persisted across tab-close-resume
via the ``onboarding_sessions`` table's credential columns.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from findajob.onboarding import OnboardingSmokeCheckFailed, inject, parse_emission
from findajob.onboarding.key_validation import (
    validate_google_format,
    validate_openrouter_format,
    validate_rapidapi_format,
)
from findajob.onboarding.openrouter_smoke import verify_openrouter_key
from findajob.onboarding.parser import ALLOWED_FILENAMES
from findajob.onboarding.session_store import (
    Credentials,
    Session,
    create_session,
    find_active,
    find_credentials_only,
    get_credentials,
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
    """True iff the in-app interview can be started right now.

    Either the tester collected their own credentials (#339 Step 1) or the
    operator opted in via ``OPENROUTER_OPERATOR_KEY`` (#336 fallback). The
    runtime check matches the precedence in
    :func:`findajob.web.routes.onboarding_interview._resolved_chat_key`.
    """
    if (os.environ.get("OPENROUTER_OPERATOR_KEY") or "").strip():
        return True
    db_path: Path | None = getattr(request.app.state, "db_path", None)
    if db_path is None:
        return False
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
    except sqlite3.Error:
        return False
    try:
        return find_credentials_only(conn) is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _active_session_for_index(request: Request) -> Session | None:
    """Look up a resumable in-app interview session for the index page.

    Returns ``None`` when:
    - In-app interview is unavailable on this stack (neither tester
      credentials collected nor ``OPENROUTER_OPERATOR_KEY`` set)
    - DB unavailable or schema doesn't include ``onboarding_sessions``
    - no recent un-completed session exists

    Failures are silent — the resume affordance is a convenience, not a
    correctness requirement, and failing the index render over a session
    lookup glitch would break the whole onboarding entry point.

    Updated in #339 to gate on the same precedence as
    :func:`_has_in_app_interview_capability` (was: env var only). Without
    this, a self-deploy stack with tester credentials but no operator env
    var would never surface the resume affordance — a tester who closes
    their tab mid-interview would have to start over.
    """
    if not _has_in_app_interview_capability(request):
        return None
    db_path: Path | None = getattr(request.app.state, "db_path", None)
    if db_path is None:
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
    except sqlite3.Error:
        return None
    try:
        return find_active(conn)
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
        conn = sqlite3.connect(str(db_path), timeout=5)
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


def _interview_prompt_path(base_root: Path) -> Path:
    return base_root / "config" / "roles" / "onboarding_interviewer.md"


def _format_parse_error(
    emission: str,
    missing: list[str],
    unknown: list[str],
) -> str:
    """Build a diagnostic error message for a failed emission parse.

    Distinguishes the three real-world failure shapes — empty paste, no
    delimited blocks at all (=> wrong content type), and partial paste
    with some blocks present — so the user gets a remedy specific to
    what actually went wrong, not a generic 'something is missing'.
    Surfaces unknown block names (likely typos) as a separate hint.
    """
    blob = emission.strip()
    found_count = len(ALLOWED_FILENAMES) - len(missing)

    if not blob:
        msg = (
            "The paste box is empty. After your LLM finishes the interview "
            "and emits the file blocks, copy the entire chat (or at least "
            "everything from the first `<<<FILE: …>>>` line to the last "
            "`<<<END FILE: …>>>` line) and paste it here."
        )
    elif found_count == 0:
        # Pasted SOMETHING, but parser found zero recognizable blocks.
        # Most often: copied just the chat-prose, missed the delimited blocks;
        # or LLM produced markdown headings instead of `<<<FILE: …>>>` markers.
        msg = (
            "We couldn't find any `<<<FILE: name>>>` … `<<<END FILE: name>>>` "
            "block in your paste. Common causes: (a) the LLM didn't actually "
            'emit the delimited file blocks — re-prompt it with "Now emit the '
            'ten file blocks per the interview spec"; (b) you copied only '
            "the chat prose and missed the blocks at the end of the "
            "transcript; (c) markdown formatting stripped the `<<<` markers — "
            "try copying from the LLM's raw-text view if it has one."
        )
    else:
        # Some blocks parsed, others didn't. Likely the LLM stopped emitting
        # mid-list, or the user's paste was truncated.
        msg = (
            f"We found {found_count} of {len(ALLOWED_FILENAMES)} required "
            "blocks, but these are still missing: "
            f"{', '.join(missing)}. Scroll through your chat to make sure "
            "every `<<<FILE: name>>> … <<<END FILE: name>>>` block is in "
            "your paste — re-prompt the LLM if it stopped early."
        )

    if unknown:
        msg += (
            f" We also found these unrecognized block names (likely typos): "
            f"{', '.join(unknown)}. If one of those was supposed to be a "
            "required block, fix the filename in your paste and re-submit."
        )

    return msg


@router.get("/onboarding/", response_class=HTMLResponse)
def onboarding_index(request: Request, mode: str = "") -> HTMLResponse:
    """Landing page. ``mode=rerun`` flips on the backup warning.

    When the stack is already onboarded (sentinel file present) AND no
    Step 1 credentials have been collected yet AND the user is not in
    rerun mode, surface a brief "you've already onboarded" hint so an
    already-configured tester who lands here from a stale link or out
    of curiosity doesn't think findajob has forgotten them. (#339
    advisor follow-up.)
    """
    templates = request.app.state.templates
    active = _active_session_for_index(request)
    creds = _credentials_for_index(request)
    keys_collected = creds is not None and (creds.openrouter_api_key is not None)

    base_root: Path = request.app.state.base_root
    is_already_onboarded = (base_root / "data" / ".onboarding-complete").is_file()
    show_already_onboarded_hint = is_already_onboarded and not keys_collected and mode != "rerun"

    return templates.TemplateResponse(
        request=request,
        name="onboarding/index.html",
        context={
            "is_rerun": mode == "rerun",
            "paste_error": None,
            "paste_content": "",
            "openrouter_api_key": "",
            "active_session_id": active.id if active else None,
            "active_session_age": _humanize_minutes_ago(active.last_turn_at) if active else None,
            "keys_collected": keys_collected,
            "openrouter_last4": _last4(creds.openrouter_api_key) if creds else "",
            "rapidapi_last4": _last4(creds.rapidapi_key) if creds else "",
            "google_last4": _last4(creds.google_api_key) if creds else "",
            "keys_error": None,
            "rapidapi_input": "",
            "google_input": "",
            "show_already_onboarded_hint": show_already_onboarded_hint,
        },
    )


def _render_keys_error(
    request: Request,
    *,
    error: str,
    rapidapi_input: str = "",
    google_input: str = "",
) -> HTMLResponse:
    """Re-render the index page with a Step 1 error, preserving optional inputs.

    OpenRouter input is intentionally NOT preserved — when verification fails
    the user typically re-pastes from the provider's key page rather than
    correcting in place, and reflowing a password-class field across requests
    invites confusion. RapidAPI / Google are preserved because the user may
    only need to fix one of them.
    """
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/index.html",
        context={
            "is_rerun": False,
            "paste_error": None,
            "paste_content": "",
            "openrouter_api_key": "",
            "active_session_id": None,
            "active_session_age": None,
            "keys_collected": False,
            "openrouter_last4": "",
            "rapidapi_last4": "",
            "google_last4": "",
            "keys_error": error,
            "rapidapi_input": rapidapi_input,
            "google_input": google_input,
            "show_already_onboarded_hint": False,
        },
        status_code=400,
    )


@router.post("/onboarding/keys", response_model=None)
def onboarding_keys(
    request: Request,
    openrouter_api_key: str = Form(default=""),
    rapidapi_key: str = Form(default=""),
    google_api_key: str = Form(default=""),
    reset: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Step 1 of #339: collect three API keys; persist to the credentials session.

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
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        if reset == "1":
            existing = find_credentials_only(conn)
            if existing is not None:
                set_credentials(
                    conn,
                    existing.id,
                    openrouter_api_key="",
                    rapidapi_key="",
                    google_api_key="",
                )
            return RedirectResponse(url="/onboarding/", status_code=303)

        ok, err = validate_openrouter_format(openrouter_api_key)
        if not ok:
            return _render_keys_error(
                request,
                error=err,
                rapidapi_input=rapidapi_key,
                google_input=google_api_key,
            )
        ok, err = validate_rapidapi_format(rapidapi_key)
        if not ok:
            return _render_keys_error(
                request,
                error=err,
                rapidapi_input=rapidapi_key,
                google_input=google_api_key,
            )
        ok, err = validate_google_format(google_api_key)
        if not ok:
            return _render_keys_error(
                request,
                error=err,
                rapidapi_input=rapidapi_key,
                google_input=google_api_key,
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
                google_input=google_api_key,
            )

        # All validations passed — UPDATE existing credentials session, or
        # INSERT a fresh one. This prevents orphan-row accumulation when a
        # user paste-typos several times before getting it right.
        existing = find_credentials_only(conn)
        if existing is not None:
            session_id = existing.id
        else:
            session_id = create_session(conn)
        set_credentials(
            conn,
            session_id,
            openrouter_api_key=openrouter_api_key.strip(),
            rapidapi_key=rapidapi_key.strip(),
            google_api_key=google_api_key.strip(),
        )
        return RedirectResponse(url="/onboarding/", status_code=303)
    finally:
        conn.close()


@router.get("/onboarding/prompt", response_class=PlainTextResponse)
def onboarding_prompt(request: Request) -> PlainTextResponse:
    """Serve the interview role verbatim so the user can copy it.

    Delivered as ``text/plain; charset=utf-8`` so "copy to clipboard" UX
    is literal — the user pastes the exact bytes we ship.
    """
    base_root: Path = request.app.state.base_root
    prompt_path = _interview_prompt_path(base_root)
    text = prompt_path.read_text(encoding="utf-8")
    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8")


@router.post("/onboarding/inject", response_model=None)
def onboarding_inject(
    request: Request,
    emission: str = Form(default=""),
    openrouter_api_key: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Parse and inject an interview emission; render completion page on success.

    The OpenRouter API key arrives in its own form field — kept out of the
    ``emission`` blob so it never enters the user's chat-LLM logs (#328).

    #339: when credentials were collected via Step 1, prefer those values
    over the form's OpenRouter input (which is rendered as a masked
    "***last4" display in that case, not an editable field) and merge the
    optional RapidAPI / Google keys into ``data/.env`` alongside.
    """
    templates = request.app.state.templates
    result = parse_emission(emission)
    if result.missing:
        paste_error = _format_parse_error(emission, result.missing, result.unknown)
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": openrouter_api_key,
                "paste_error": paste_error,
            },
            status_code=400,
        )

    # #339: pull credentials from the Step 1 session if present and let them
    # override form-supplied values. The paste-back form's OpenRouter input
    # is read-only when credentials exist, but a direct POST (e.g. from an
    # integration test) is still a supported entry point — fall back to the
    # form value for that legacy path.
    creds = _credentials_for_index(request)
    resolved_or = (creds.openrouter_api_key if creds and creds.openrouter_api_key else openrouter_api_key).strip()
    resolved_rapid = (creds.rapidapi_key if creds and creds.rapidapi_key else "").strip()
    resolved_google = (creds.google_api_key if creds and creds.google_api_key else "").strip()

    if not resolved_or:
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": "",
                "paste_error": (
                    "OpenRouter API key is missing. Paste your key (starts with sk-or-v1-…) "
                    "from https://openrouter.ai/keys into the API key field above the paste box, "
                    "then click Inject again. The key is required so we can verify it works "
                    "before sealing your stack — failing to verify here would let the pipeline "
                    "silently break on first scheduled triage."
                ),
            },
            status_code=400,
        )
    base_root: Path = request.app.state.base_root
    try:
        inject_result = inject(
            base_root,
            result.found,
            openrouter_api_key=resolved_or,
            rapidapi_key=resolved_rapid,
            google_api_key=resolved_google,
        )
    except OnboardingSmokeCheckFailed as e:
        # Files were committed; only the sentinel is missing. The next paste-back
        # with a corrected key will overwrite cleanly. Render the user-facing
        # error so they can see what went wrong. e.user_message is already a
        # specific, actionable string — surface it without further wrapping.
        # When credentials came from Step 1 (creds is not None), the form's
        # OpenRouter input is read-only and the user fixes the key by clicking
        # "Change keys" — surface that path explicitly in the error message.
        if creds and creds.openrouter_api_key:
            error_msg = (
                "OpenRouter rejected the key when we tried to verify it. "
                f"{e.user_message} Use 'Change keys' at the top of the page to "
                "re-supply your key — your paste content is preserved above."
            )
            preserve_input = ""
        else:
            error_msg = (
                "OpenRouter rejected the key when we tried to verify it. "
                f"{e.user_message} "
                "Fix the key and click Inject again — your paste content is preserved above."
            )
            preserve_input = openrouter_api_key
        return templates.TemplateResponse(
            request=request,
            name="onboarding/index.html",
            context={
                "is_rerun": False,
                "paste_content": emission,
                "openrouter_api_key": preserve_input,
                "paste_error": error_msg,
            },
            status_code=400,
        )
    # Clear cached guard state so the next /board/ request passes through
    request.app.state.onboarding_complete = True
    return templates.TemplateResponse(
        request=request,
        name="onboarding/complete.html",
        context={
            "discovery_success": inject_result.discovery.success,
            "discovery_count": inject_result.discovery.count,
            "discovery_error": inject_result.discovery.error,
        },
    )
