"""In-app onboarding interview routes (#336 + #339).

Wires session_store + interview_runner + parser + injector into a chat
surface so non-technical testers can complete onboarding without leaving
findajob's UI.

Routes always register (#339); the in-app affordance is gated at runtime
on tester credentials being collected (Step 1 of /onboarding/). When no
credentials are present the routes return 503 with an actionable error
pointing the user back to /onboarding/ Step 1.

Cross-task constraints (from #336 Session 2026-05-01):
- Emission detection runs against the cumulative assistant transcript on every
  turn, driven by :data:`findajob.onboarding.parser.ALLOWED_FILENAMES` (NEVER
  hardcoded counts) so #212 / #283 changes land cleanly.
- Finalize reads the user's OpenRouter key from collected credentials when
  available (#339); the form-supplied key is a legacy safety net only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.cost_tracking import log_call, role_model
from findajob.onboarding import OnboardingSmokeCheckFailed, inject
from findajob.onboarding.interview_runner import InterviewRunnerError, run_turn
from findajob.onboarding.parser import ALLOWED_FILENAMES, parse_emission
from findajob.onboarding.session_store import (
    add_turn_cost,
    append_turn,
    find_active,
    find_credentials_only,
    get_credentials,
    get_session,
    mark_complete,
    set_error,
    update_captured_blocks,
)
from findajob.utils import log_event
from findajob.web.markdown import render_chat_assistant_html

_INTERVIEWER_MODEL = role_model("onboarding_interviewer")

router = APIRouter()

_KICKOFF_USER_MESSAGE = "Begin the interview."


def _resolved_chat_key(conn: sqlite3.Connection, session_id: str | None) -> str:
    """Return the OpenRouter key for chat-runner calls.

    Reads the tester's own key in precedence order:

    1. The tester's own key on the given session (if session_id provided
       and credentials set on it).
    2. The most-recent credentials-only session's OpenRouter key (when
       called from /start before a chat session exists).
    3. Empty string — caller surfaces a 503 with link back to /onboarding/.

    Tester pays for their own chat — there is no operator-funded fallback.
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
    return ""


def _unavailable_503() -> HTTPException:
    """Consistent 503 surface for "in-app interview unavailable" cases.

    Detail message points the user at /onboarding/ Step 1 — the only path
    out of this state is to supply tester credentials.
    """
    return HTTPException(
        status_code=503,
        detail=(
            "In-app interview unavailable: no OpenRouter key on file for this stack. "
            "Visit /onboarding/ to provide your API keys, then return here to begin "
            "the interview."
        ),
    )


def _conn(request: Request) -> sqlite3.Connection:
    db_path: Path = request.app.state.db_path
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


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


def _keys_collected_for(conn: sqlite3.Connection, session_id: str) -> tuple[bool, str]:
    """Return ``(keys_collected, openrouter_last4)`` for finalize-form rendering.

    True iff the session has a non-NULL ``tester_openrouter_key``. Templates
    use this to hide the finalize OR-input field when Step 1 already has
    the key — typing a different one at finalize broke the smoke check
    and stranded the user on an unfinishable session (the loop-back bug).
    """
    creds = get_credentials(conn, session_id)
    if creds is None or not creds.openrouter_api_key:
        return False, ""
    return True, creds.openrouter_api_key[-4:]


def _render_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return a copy of history where each assistant turn has ``rendered_content``.

    User turns are passed through unchanged (the template auto-escapes them).
    Assistant turns get ``rendered_content`` set to the output of
    :func:`render_chat_assistant_html` — FILE blocks become badge spans and
    the text is rendered through Python-Markdown.

    Parser invariant: this is render-only.  The parser reads ``session.history``
    (the raw stored turns), not this rendered list.
    """
    result: list[dict[str, str]] = []
    for turn in history:
        if turn.get("role") == "assistant":
            result.append({**turn, "rendered_content": render_chat_assistant_html(turn["content"])})
        else:
            result.append(turn)
    return result


def _render_chat(
    request: Request,
    *,
    session_id: str,
    history: list[dict[str, str]],
    captured: dict[str, str],
    keys_collected: bool = False,
    openrouter_last4: str = "",
    cumulative_cost_usd: float = 0.0,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding/interview.html",
        context={
            "session_id": session_id,
            "history": _render_history(history),
            "captured_count": len(captured),
            "required_count": len(ALLOWED_FILENAMES),
            "finalize_ready": len(captured) >= len(ALLOWED_FILENAMES),
            "keys_collected": keys_collected,
            "openrouter_last4": openrouter_last4,
            "cumulative_cost_usd": cumulative_cost_usd,
            "error": error,
        },
        status_code=status_code,
    )


@router.post("/onboarding/interview/start", response_model=None)
def start_interview(request: Request) -> HTMLResponse | RedirectResponse:
    """Create a session, run the synthetic kickoff turn, redirect to the chat page.

    Step 1 (API key collection at ``/onboarding/keys``) is required before
    starting — promote that credentials-only session row into the active
    interview rather than creating a fresh one. If no credentials row
    exists, 503 back to /onboarding/.
    """
    conn = _conn(request)
    try:
        # Re-clicking "Start interview" while an interview is already in flight
        # (e.g. user came back to /onboarding/ on a different tab) should land
        # them on the existing chat, not 503 because there's no longer a
        # credentials-only row to promote. Note: a credentials-only row also
        # satisfies find_active() (no completed_at, recent last_turn_at), so
        # only redirect when the row has at least one turn already.
        existing_active = find_active(conn)
        if existing_active is not None and existing_active.history:
            return RedirectResponse(
                url=f"/onboarding/interview/{existing_active.id}",
                status_code=303,
            )

        cred_session = find_credentials_only(conn)
        if cred_session is None:
            raise _unavailable_503()
        session_id = cred_session.id

        chat_key = _resolved_chat_key(conn, session_id)
        if not chat_key:
            raise _unavailable_503()
        keys_collected, openrouter_last4 = _keys_collected_for(conn, session_id)

        try:
            assistant_text, usage = run_turn(
                api_key=chat_key,
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
                keys_collected=keys_collected,
                openrouter_last4=openrouter_last4,
                error=e.user_message,
                status_code=200,
            )

        add_turn_cost(conn, session_id, usage)
        # Write cost_log row — subsumes #463 for onboarding turns.
        try:
            log_call(
                conn,
                job_id=None,
                operation="onboarding_interviewer",
                model=_INTERVIEWER_MODEL,
                input_text=_KICKOFF_USER_MESSAGE,
                output_text=assistant_text,
                latency_ms=None,
                success=True,
                cost_usd_override=float(usage.get("cost") or 0.0),
                input_tokens_override=int(usage.get("prompt_tokens") or 0),
                output_tokens_override=int(usage.get("completion_tokens") or 0),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            log_event(
                "cost_log_failed",
                operation="onboarding_interviewer",
                route="start",
                error=f"{type(e).__name__}: {e}",
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
            assistant_text, usage = run_turn(
                api_key=chat_key,
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

        add_turn_cost(conn, session_id, usage)
        # Write cost_log row — subsumes #463 for onboarding turns.
        try:
            log_call(
                conn,
                job_id=None,
                operation="onboarding_interviewer",
                model=_INTERVIEWER_MODEL,
                input_text=message,
                output_text=assistant_text,
                latency_ms=None,
                success=True,
                cost_usd_override=float(usage.get("cost") or 0.0),
                input_tokens_override=int(usage.get("prompt_tokens") or 0),
                output_tokens_override=int(usage.get("completion_tokens") or 0),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            log_event(
                "cost_log_failed",
                operation="onboarding_interviewer",
                route="turn",
                error=f"{type(e).__name__}: {e}",
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

        keys_collected, openrouter_last4 = _keys_collected_for(conn, session_id)
        # Re-read the session to pick up the cumulative cost we just added.
        refreshed = get_session(conn, session_id)
        cumulative_cost = refreshed.cumulative_cost_usd if refreshed else 0.0

        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="onboarding/_turn.html",
            context={
                "session_id": session_id,
                "user_message": message,
                "assistant_message": assistant_text,
                "assistant_message_html": render_chat_assistant_html(assistant_text),
                "captured_count": len(captured),
                "required_count": len(ALLOWED_FILENAMES),
                "finalize_ready": len(captured) >= len(ALLOWED_FILENAMES),
                "keys_collected": keys_collected,
                "openrouter_last4": openrouter_last4,
                "cumulative_cost_usd": cumulative_cost,
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
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        keys_collected, openrouter_last4 = _keys_collected_for(conn, session_id)
    finally:
        conn.close()
    return _render_chat(
        request,
        session_id=session_id,
        history=sess.history,
        captured=sess.captured_blocks,
        keys_collected=keys_collected,
        openrouter_last4=openrouter_last4,
        cumulative_cost_usd=sess.cumulative_cost_usd,
        error=sess.error_state,
    )


@router.post("/onboarding/interview/{session_id}/finalize", response_model=None)
def finalize_interview(
    request: Request,
    session_id: str,
) -> HTMLResponse | RedirectResponse:
    """Validate captured blocks, run :func:`inject`, mark session complete.

    Keys come from the credentials bound to this session at /onboarding/
    Step 1 — that's the single collection point. The earlier form-input
    fallback existed for the paste-back path; it has been retired in
    favor of mandatory Step 1.
    """
    conn = _conn(request)
    try:
        sess = get_session(conn, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")

        keys_collected, openrouter_last4 = _keys_collected_for(conn, session_id)
        cumulative_cost = sess.cumulative_cost_usd

        missing = [name for name in ALLOWED_FILENAMES if name not in sess.captured_blocks]
        if missing:
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                keys_collected=keys_collected,
                openrouter_last4=openrouter_last4,
                cumulative_cost_usd=cumulative_cost,
                error=(
                    f"Interview not yet complete — still missing {len(missing)} of "
                    f"{len(ALLOWED_FILENAMES)} required blocks: {', '.join(missing)}. "
                    "Continue the conversation until every block has been emitted."
                ),
                status_code=400,
            )

        creds = get_credentials(conn, session_id)
        if creds is None or not creds.openrouter_api_key:
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                keys_collected=False,
                openrouter_last4="",
                cumulative_cost_usd=cumulative_cost,
                error=(
                    "Your OpenRouter key was cleared from this session. Go back to "
                    "/onboarding/ Step 1, save your keys again, then return here and "
                    "click Finalize."
                ),
                status_code=400,
            )

        base_root: Path = request.app.state.base_root
        try:
            inject_result = inject(
                base_root,
                sess.captured_blocks,
                openrouter_api_key=creds.openrouter_api_key.strip(),
                rapidapi_key=(creds.rapidapi_key or "").strip(),
            )
        except OnboardingSmokeCheckFailed as e:
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                keys_collected=keys_collected,
                openrouter_last4=openrouter_last4,
                cumulative_cost_usd=cumulative_cost,
                error=(
                    "OpenRouter rejected the key when we tried to verify it. "
                    f"{e.user_message} Use 'Change keys' on /onboarding/ to "
                    "supply a different key, then return here and click Finalize."
                ),
                status_code=400,
            )

        mark_complete(conn, session_id)
    finally:
        conn.close()

    # When the chosen adapter's env var is missing, gate to the feed-config
    # step where the user can enter the key and run a live test. From there
    # the user proceeds to the Gmail-config gate (#407), which is the
    # universal terminal step that writes the sentinel.
    if inject_result.decision.gate_to_feed_config:
        return RedirectResponse(f"/onboarding/feed-config/{session_id}", status_code=303)

    # No feed-config gate — redirect straight to the Gmail-config gate (#407).
    # The sentinel is not yet written; gmail-config /finish writes it after
    # the user saves+verifies an IMAP credential pair or explicitly skips.
    return RedirectResponse(f"/onboarding/gmail-config/{session_id}/", status_code=303)
