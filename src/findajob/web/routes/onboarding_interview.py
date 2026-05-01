"""In-app onboarding interview routes (#336 + #339).

Wires session_store + interview_runner + parser + injector into a chat
surface so non-technical testers can complete onboarding without leaving
findajob's UI.

Routes always register (#339); the in-app affordance is gated at runtime
on either tester credentials being collected (Step 1 of /onboarding/) OR
``OPENROUTER_OPERATOR_KEY`` being set (the operator-funded fallback used
by ``findajob-test`` and operator-deployed-for-tester scenarios). When
neither is available the routes return 503 with an actionable error
pointing the user back to /onboarding/ Step 1.

Cross-task constraints (from #336 Session 2026-05-01):
- Emission detection runs against the cumulative assistant transcript on every
  turn, driven by :data:`findajob.onboarding.parser.ALLOWED_FILENAMES` (NEVER
  hardcoded counts) so #212 / #283 changes land cleanly.
- Finalize reads the user's OpenRouter key from collected credentials when
  available (#339); the form-supplied key is a legacy safety net only.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.onboarding import OnboardingSmokeCheckFailed, inject
from findajob.onboarding.interview_runner import InterviewRunnerError, run_turn
from findajob.onboarding.parser import ALLOWED_FILENAMES, parse_emission
from findajob.onboarding.session_store import (
    append_turn,
    create_session,
    find_credentials_only,
    get_credentials,
    get_session,
    mark_complete,
    set_error,
    update_captured_blocks,
)
from findajob.utils import log_event

router = APIRouter()

OPERATOR_KEY_ENV = "OPENROUTER_OPERATOR_KEY"
_SYSTEM_PROMPT_RELPATH = Path("config") / "roles" / "onboarding_interviewer.md"
_KICKOFF_USER_MESSAGE = "Begin the interview."


def _operator_key() -> str:
    return (os.environ.get(OPERATOR_KEY_ENV) or "").strip()


def _resolved_chat_key(conn: sqlite3.Connection, session_id: str | None) -> str:
    """Return the OpenRouter key for chat-runner calls, in precedence order:

    1. The tester's own key on the given session (if session_id provided
       and credentials set on it).
    2. The most-recent credentials-only session's OpenRouter key (when
       called from /start before a chat session exists).
    3. The operator-funded env var (``OPENROUTER_OPERATOR_KEY``) when set.
    4. Empty string — caller surfaces a 503 with link back to /onboarding/.

    The two-phase lookup (session-specific then credentials-only) is what
    lets a tester start an interview, supply credentials in the same UI,
    and not need them re-attached to every chat session row that exists.
    Once a chat session is created the credentials are bound to that
    session via :func:`session_store.set_credentials` so subsequent turns
    don't need to re-resolve from credentials_only.
    """
    if session_id is not None:
        creds = get_credentials(conn, session_id)
        if creds is not None and creds.openrouter_api_key:
            return creds.openrouter_api_key
    fallback_session = find_credentials_only(conn)
    if fallback_session is not None:
        creds = get_credentials(conn, fallback_session.id)
        if creds is not None and creds.openrouter_api_key:
            return creds.openrouter_api_key
    return _operator_key()


def _unavailable_503() -> HTTPException:
    """Consistent 503 surface for "in-app interview unavailable" cases.

    Detail message points the user at /onboarding/ Step 1 — the only path
    out of this state is to either supply tester credentials or set
    ``OPENROUTER_OPERATOR_KEY`` on the stack.
    """
    return HTTPException(
        status_code=503,
        detail=(
            "In-app interview unavailable: no OpenRouter key resolved for this stack. "
            "Visit /onboarding/ to provide your API keys, or have the operator set "
            "OPENROUTER_OPERATOR_KEY for an operator-funded interview."
        ),
    )


def _conn(request: Request) -> sqlite3.Connection:
    db_path: Path = request.app.state.db_path
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _system_prompt(request: Request) -> str:
    base_root: Path = request.app.state.base_root
    return (base_root / _SYSTEM_PROMPT_RELPATH).read_text(encoding="utf-8")


def _captured_from_history(history: list[dict[str, str]]) -> dict[str, str]:
    """Run :func:`parse_emission` over the cumulative assistant transcript.

    LLMs occasionally split emission blocks across turns, so we re-scan
    the full assistant transcript on every turn rather than just the latest
    message. ``parse_emission`` is idempotent — re-running it cannot lose
    blocks that were captured earlier.
    """
    transcript = "\n\n".join(turn["content"] for turn in history if turn.get("role") == "assistant")
    return parse_emission(transcript).found


def _log_runner_error(*, session_id: str, route: str, err: InterviewRunnerError) -> None:
    """Emit a structured pipeline.jsonl event for every runner failure.

    Failure modes here are upstream (operator credit, rate limit, network)
    — surfacing them in the same log used for triage / scoring lets the
    operator correlate "interview died at 14:02" with other infra signals.
    """
    log_event(
        "onboarding_interview_error",
        session_id=session_id,
        route=route,
        error_kind=err.kind,
        status_code=err.status_code,
    )


def _render_error_partial(
    request: Request,
    *,
    session_id: str,
    last_message: str,
    err: InterviewRunnerError,
) -> HTMLResponse:
    """Render the per-turn error partial (HTMX-appended into #messages).

    Status code is intentionally 200: HTMX's default config silently
    drops 4xx/5xx responses — using 200 lets the swap go through and the
    user sees the actionable error bubble + Try Again button.
    """
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/_turn_error.html",
        context={
            "session_id": session_id,
            "last_message": last_message,
            "error_kind": err.kind,
            "error_message": err.user_message,
            "status_code": err.status_code,
        },
    )


def _render_chat(
    request: Request,
    *,
    session_id: str,
    history: list[dict[str, str]],
    captured: dict[str, str],
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/interview.html",
        context={
            "session_id": session_id,
            "history": history,
            "captured_count": len(captured),
            "required_count": len(ALLOWED_FILENAMES),
            "finalize_ready": len(captured) >= len(ALLOWED_FILENAMES),
            "error": error,
        },
        status_code=status_code,
    )


@router.post("/onboarding/interview/start", response_model=None)
def start_interview(request: Request) -> HTMLResponse | RedirectResponse:
    """Create a session, run the synthetic kickoff turn, redirect to the chat page.

    Resolution path (#339):

    - Look up the credentials-only session to "promote" into an active
      interview if one exists. This binds the tester's collected
      OpenRouter key to the same session row that gets the chat history.
    - If no credentials-only session exists but ``OPENROUTER_OPERATOR_KEY``
      is set, create a fresh session and use the operator-funded key. The
      session has no credentials attached — finalize will collect the
      OpenRouter key as a paste-back-style safety net.
    - If neither, 503 with a pointer back to /onboarding/.
    """
    conn = _conn(request)
    try:
        # Promote credentials-only session into the active interview when
        # present, so the chat history attaches to the same row that holds
        # the tester's key. Fresh-create otherwise.
        cred_session = find_credentials_only(conn)
        if cred_session is not None:
            session_id = cred_session.id
        else:
            session_id = create_session(conn)

        chat_key = _resolved_chat_key(conn, session_id)
        if not chat_key:
            raise _unavailable_503()

        try:
            assistant_text, _usage = run_turn(
                operator_key=chat_key,
                system_prompt=_system_prompt(request),
                history=[],
                user_message=_KICKOFF_USER_MESSAGE,
            )
        except InterviewRunnerError as e:
            set_error(conn, session_id, e.user_message)
            _log_runner_error(session_id=session_id, route="start", err=e)
            # /start is the very first turn — no chat to splice an error
            # bubble into yet, so render the full chat page seeded with
            # the error banner. /turn renders the OOB partial instead.
            return _render_chat(
                request,
                session_id=session_id,
                history=[],
                captured={},
                error=e.user_message,
                status_code=200,
            )

        append_turn(conn, session_id, "user", _KICKOFF_USER_MESSAGE)
        append_turn(conn, session_id, "assistant", assistant_text)
        captured = _captured_from_history(
            [
                {"role": "user", "content": _KICKOFF_USER_MESSAGE},
                {"role": "assistant", "content": assistant_text},
            ]
        )
        if captured:
            update_captured_blocks(conn, session_id, captured)
    finally:
        conn.close()

    return RedirectResponse(url=f"/onboarding/interview/{session_id}", status_code=303)


@router.post("/onboarding/interview/turn", response_class=HTMLResponse)
def post_turn(
    request: Request,
    session_id: str = Form(...),
    message: str = Form(...),
) -> HTMLResponse:
    """Append a user turn, call ``run_turn``, persist + scan the assistant reply."""
    conn = _conn(request)
    try:
        sess = get_session(conn, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")

        chat_key = _resolved_chat_key(conn, session_id)
        if not chat_key:
            raise _unavailable_503()

        try:
            assistant_text, _usage = run_turn(
                operator_key=chat_key,
                system_prompt=_system_prompt(request),
                history=sess.history,
                user_message=message,
            )
        except InterviewRunnerError as e:
            set_error(conn, session_id, e.user_message)
            _log_runner_error(session_id=session_id, route="turn", err=e)
            return _render_error_partial(
                request,
                session_id=session_id,
                last_message=message,
                err=e,
            )

        append_turn(conn, session_id, "user", message)
        append_turn(conn, session_id, "assistant", assistant_text)

        new_history = sess.history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": assistant_text},
        ]
        captured = _captured_from_history(new_history)
        if captured != sess.captured_blocks:
            update_captured_blocks(conn, session_id, captured)

        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="onboarding/_turn.html",
            context={
                "session_id": session_id,
                "user_message": message,
                "assistant_message": assistant_text,
                "captured_count": len(captured),
                "required_count": len(ALLOWED_FILENAMES),
                "finalize_ready": len(captured) >= len(ALLOWED_FILENAMES),
            },
        )
    finally:
        conn.close()


@router.get("/onboarding/interview/{session_id}", response_class=HTMLResponse)
def resume_interview(request: Request, session_id: str) -> HTMLResponse:
    """Render the full chat UI seeded with the persisted history."""
    conn = _conn(request)
    try:
        sess = get_session(conn, session_id)
    finally:
        conn.close()
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _render_chat(
        request,
        session_id=session_id,
        history=sess.history,
        captured=sess.captured_blocks,
        error=sess.error_state,
    )


@router.post("/onboarding/interview/{session_id}/finalize", response_model=None)
def finalize_interview(
    request: Request,
    session_id: str,
    openrouter_api_key: str = Form(default=""),
    rapidapi_key: str = Form(default=""),
    google_api_key: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    """Validate captured blocks, run :func:`inject`, mark session complete.

    Resolution path for the keys passed to :func:`inject` (#339):

    - Prefer credentials already collected on this session via Step 1.
    - Form-supplied values are a legacy safety net used when credentials
      were never collected (e.g. operator-funded path with no Step 1).
    - Whichever source wins, the values flow into the per-stack
      ``data/.env`` merge.
    """
    conn = _conn(request)
    try:
        sess = get_session(conn, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")

        missing = [name for name in ALLOWED_FILENAMES if name not in sess.captured_blocks]
        if missing:
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                error=(
                    f"Interview not yet complete — still missing {len(missing)} of "
                    f"{len(ALLOWED_FILENAMES)} required blocks: {', '.join(missing)}. "
                    "Continue the conversation until every block has been emitted."
                ),
                status_code=400,
            )

        # #339: prefer collected credentials over form-supplied values.
        creds = get_credentials(conn, session_id)
        resolved_or = (creds.openrouter_api_key if creds and creds.openrouter_api_key else openrouter_api_key).strip()
        resolved_rapid = (creds.rapidapi_key if creds and creds.rapidapi_key else rapidapi_key).strip()
        resolved_google = (creds.google_api_key if creds and creds.google_api_key else google_api_key).strip()

        if not resolved_or:
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                error=(
                    "OpenRouter API key is missing. Paste your personal key (starts "
                    "with sk-or-v1-…) from https://openrouter.ai/keys into the API key "
                    "field, then click Finalize again. The key is required so we can "
                    "verify it works before sealing your stack."
                ),
                status_code=400,
            )

        base_root: Path = request.app.state.base_root
        try:
            inject_result = inject(
                base_root,
                sess.captured_blocks,
                openrouter_api_key=resolved_or,
                rapidapi_key=resolved_rapid,
                google_api_key=resolved_google,
            )
        except OnboardingSmokeCheckFailed as e:
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                error=(
                    "OpenRouter rejected the key when we tried to verify it. "
                    f"{e.user_message} Fix the key and click Finalize again — your "
                    "interview history is preserved."
                ),
                status_code=400,
            )

        mark_complete(conn, session_id)
    finally:
        conn.close()

    # Mirror the existing /onboarding/inject contract: clear the cached guard
    # state and render complete.html inline. No /onboarding/complete GET
    # route exists yet, so a redirect would 404.
    request.app.state.onboarding_complete = True
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/complete.html",
        context={
            "discovery_success": inject_result.discovery.success,
            "discovery_count": inject_result.discovery.count,
            "discovery_error": inject_result.discovery.error,
        },
    )
