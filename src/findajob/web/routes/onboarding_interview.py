"""In-app onboarding interview routes (#336 + #339).

Wires session_store + interview_runner + parser + injector into a chat
surface so non-technical users can complete onboarding without leaving
findajob's UI.

Routes always register (#339); the in-app affordance is gated at runtime
on user credentials being collected (Step 1 of /onboarding/). When no
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

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from findajob.audit import log_event
from findajob.cost_tracking import log_call, role_model
from findajob.db import connect
from findajob.llm.openrouter import (
    LLMSpendCeilingExceeded,
    OpenRouterError,
    StreamCaptured,
    StreamError,
    StreamFinish,
    complete_stream,
)
from findajob.onboarding import OnboardingSmokeCheckFailed, inject
from findajob.onboarding.interview_runner import InterviewRunnerError, _translate, run_turn
from findajob.onboarding.parser import ALLOWED_FILENAMES, parse_emission
from findajob.onboarding.session_store import (
    add_turn_cost,
    append_turn,
    clear_error,
    find_active,
    find_credentials_only,
    get_credentials,
    get_session,
    mark_complete,
    set_error,
    update_captured_blocks,
)
from findajob.spend_ceiling import check_call_gate
from findajob.web.markdown import render_chat_assistant_html
from findajob.web.middleware import SCOPE_KEY as _DISCONNECT_SCOPE_KEY

_INTERVIEWER_MODEL = role_model("onboarding_interviewer")

router = APIRouter()

_KICKOFF_USER_MESSAGE = "Begin the interview."


def _resolved_chat_key(conn: sqlite3.Connection, session_id: str | None) -> str:
    """Return the OpenRouter key for chat-runner calls.

    Reads the user's own key in precedence order:

    1. The user's own key on the given session (if session_id provided
       and credentials set on it).
    2. The most-recent credentials-only session's OpenRouter key (when
       called from /start before a chat session exists).
    3. Empty string — caller surfaces a 503 with link back to /onboarding/.

    User pays for their own chat — there is no operator-funded fallback.
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
    out of this state is to supply user credentials.
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
    conn = connect(db_path, timeout=30)
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

    True iff the session has a non-NULL ``user_openrouter_key``. Templates
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
            "captured_count": sum(1 for name in ALLOWED_FILENAMES if name in captured),
            "required_count": len(ALLOWED_FILENAMES),
            "finalize_ready": all(name in captured for name in ALLOWED_FILENAMES),
            "keys_collected": keys_collected,
            "openrouter_last4": openrouter_last4,
            "cumulative_cost_usd": cumulative_cost_usd,
            "error": error,
            # #755: the chat page's JS auto-fires this kickoff message
            # against /turn-stream when history is empty. Constant lives in
            # this module (server-authoritative) and is exposed via
            # data-kickoff-message on the chat container — no JS copy.
            "kickoff_message": _KICKOFF_USER_MESSAGE,
        },
        status_code=status_code,
    )


@router.post("/onboarding/interview/start", response_model=None)
def start_interview(request: Request) -> RedirectResponse:
    """Promote the credentials-only session into an interview, redirect to chat.

    #755: the greeting LLM call is deferred to ``/turn-stream`` — auto-fired
    by the chat page on load. ``/start`` is now a fast session-resolve + 303
    (<1s) instead of blocking on the synchronous run_turn() that previously
    generated the first assistant message (~25-28s cold-model latency).

    Step 1 (API-key collection at ``/onboarding/keys``) is still mandatory:
    if no session with credentials exists, 503 back to /onboarding/.
    """
    conn = _conn(request)
    try:
        # find_active matches sessions with OR without history (an empty-history
        # row created by Step 1 satisfies it too); find_credentials_only is the
        # fallback for first-/start when last_turn_at predates the 24h window.
        # Both shapes converge to "redirect to the same session id" — there's
        # no separate "promote" step now that we don't write turns here.
        session = find_active(conn) or find_credentials_only(conn)
        if session is None:
            raise _unavailable_503()
        # Validate credentials resolve here so /start 503s instead of
        # redirecting to a chat page that would immediately fail when
        # /turn-stream tries to use the same key.
        if not _resolved_chat_key(conn, session.id):
            raise _unavailable_503()
        session_id = session.id
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

        # #623: clear any prior error_state so /resume no longer renders
        # the stale banner now that this turn has self-corrected.
        clear_error(conn, session_id)
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
                "captured_count": sum(1 for name in ALLOWED_FILENAMES if name in captured),
                "required_count": len(ALLOWED_FILENAMES),
                "finalize_ready": all(name in captured for name in ALLOWED_FILENAMES),
                "keys_collected": keys_collected,
                "openrouter_last4": openrouter_last4,
                "cumulative_cost_usd": cumulative_cost,
            },
        )
    finally:
        conn.close()


# ── SSE streaming variant ─────────────────────────────────────────────────────


def _sse_event(event_type: str, data: dict) -> bytes:
    """Format a Server-Sent Event line block.

    Each event is: ``event: <type>\\ndata: <json>\\n\\n``
    """
    return f"event: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def _kickoff_error_kind(was_kickoff: bool, original_kind: str) -> dict[str, str]:
    """Return the kind discriminator fields for an SSE error event.

    #755: when the kickoff turn (the very first /turn-stream call against an
    empty-history session — auto-fired by the chat page on load) errors out,
    the SSE ``kind`` is promoted to ``'kickoff_failed'`` so the JS handler
    can render a "Retry greeting" affordance instead of a generic banner.
    The underlying cause is preserved as ``original_kind`` for the UI to
    surface inside the retry affordance.

    For post-kickoff turns, ``kind`` is the original error kind and
    ``original_kind`` is omitted (no semantic content for the JS).
    """
    if was_kickoff:
        return {"kind": "kickoff_failed", "original_kind": original_kind}
    return {"kind": original_kind}


def _stream_turn(
    *,
    db_path: Path,
    session_id: str,
    chat_key: str,
    message: str,
    sess_history: list[dict],
    sess_captured: dict,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[bytes]:
    """Drive ``complete_stream()`` and yield formatted SSE events.

    Opens its own SQLite connection — the outer route's connection is closed
    in ``finally`` before ``StreamingResponse`` iterates this generator.

    Chunk sequence:

    - ``captured`` SSE event for each :class:`~findajob.llm.openrouter.StreamCaptured` chunk.
    - ``finish`` SSE event with pre-rendered ``assistant_html``, cost, and
      progress fields on success — writes cost_log, append_turn × 2,
      update_captured_blocks at that point.
    - ``error`` SSE event (and ``set_error``) on failure paths:
      mid-stream error chunk, or ``finish_reason == "length"``.

    Client disconnect (#743): ``is_cancelled`` is polled inside
    ``complete_stream``'s SSE-read loop. On True, ``complete_stream`` returns
    WITHOUT yielding a terminal ``finish`` or ``error`` chunk. The for-loop
    here exits naturally and persistence (cost_log, append_turn,
    update_captured_blocks) is automatically skipped — those calls live
    inside the ``finish`` branch which never executes. A ``stream_cancelled``
    event is logged so the operator can correlate cancelled streams with
    pipeline events.
    """
    conn = connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    saw_terminal = False
    # #755 kickoff discriminator: an LLM call against an empty-history session
    # IS the kickoff turn (the chat page auto-fires this on load). Any error
    # path below promotes its SSE ``kind`` to ``'kickoff_failed'`` so the JS
    # can render a "Retry greeting" affordance. Captured once before the
    # complete_stream loop so persistence below (which mutates history)
    # doesn't flip the meaning mid-stream.
    was_kickoff = not sess_history
    try:
        try:
            # #755 kickoff-replay guard: defense-in-depth idempotency check
            # for the chat page's auto-fire kickoff. The page only auto-fires
            # when history is empty, but a concurrent second-tab fire (or
            # back/forward nav mid-stream) can race past that client-side
            # check. The session row's history is the canonical lock — if
            # it already carries turns, the kickoff has already fired and
            # the second attempt is a replay. Refuse without touching the
            # LLM or persisting anything; the existing greeting is already
            # in history and will re-render on the next page load.
            if message == _KICKOFF_USER_MESSAGE and sess_history:
                saw_terminal = True
                yield _sse_event(
                    "error",
                    {
                        "kind": "kickoff_replay",
                        "user_message": message,
                        "message": "The interview has already begun.",
                    },
                )
                return

            for chunk in complete_stream(
                role="onboarding_interviewer",
                prompt=message,
                cache_system=True,
                pin_provider="anthropic",
                history=sess_history,
                api_key=chat_key,
                is_cancelled=is_cancelled,
            ):
                chunk_type = chunk["type"]

                if chunk_type == "captured":
                    captured_chunk = cast(StreamCaptured, chunk)
                    yield _sse_event("captured", {"name": captured_chunk["name"]})

                elif chunk_type == "finish":
                    saw_terminal = True
                    finish_chunk = cast(StreamFinish, chunk)
                    finish_reason = finish_chunk.get("finish_reason")
                    assistant_text = finish_chunk["text"]
                    usage = finish_chunk["usage"]  # StreamUsage TypedDict

                    # finish_reason="length" mirrors run_turn's raise before
                    # persistence — no append_turn, no cost_log, just error SSE.
                    if finish_reason == "length":
                        err = _translate(OpenRouterError("max_tokens cap hit", kind="length"))
                        set_error(conn, session_id, err.user_message)
                        conn.commit()
                        yield _sse_event(
                            "error",
                            {
                                **_kickoff_error_kind(was_kickoff, "length"),
                                "user_message": message,
                                "message": err.user_message,
                            },
                        )
                        return

                    # --- Successful finish: persist turn + costs ---

                    # Clear any prior error_state (#623 parity with /turn).
                    clear_error(conn, session_id)

                    # Translate StreamUsage shape → run_turn-shape dict so
                    # add_turn_cost can read the "cost" key it expects.
                    usage_compat: dict = {
                        "prompt_tokens": usage["prompt_tokens"],
                        "completion_tokens": usage["completion_tokens"],
                        "cached_tokens": usage["cached_tokens"],
                        "cost": usage["cost_usd"],
                        "generation_id": finish_chunk.get("generation_id"),
                    }
                    add_turn_cost(conn, session_id, usage_compat)

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
                            cost_usd_override=float(usage["cost_usd"]),
                            input_tokens_override=int(usage["prompt_tokens"]),
                            output_tokens_override=int(usage["completion_tokens"]),
                        )
                        conn.commit()
                    except Exception as e:  # noqa: BLE001
                        log_event(
                            "cost_log_failed",
                            operation="onboarding_interviewer",
                            route="turn-stream",
                            error=f"{type(e).__name__}: {e}",
                        )

                    append_turn(conn, session_id, "user", message)
                    append_turn(conn, session_id, "assistant", assistant_text)

                    new_history = sess_history + [
                        {"role": "user", "content": message},
                        {"role": "assistant", "content": assistant_text},
                    ]
                    captured = _captured_from_history(new_history)
                    if captured != sess_captured:
                        update_captured_blocks(conn, session_id, captured)
                    conn.commit()

                    # Re-read session to get the updated cumulative cost.
                    refreshed = get_session(conn, session_id)
                    cumulative_cost = refreshed.cumulative_cost_usd if refreshed else 0.0

                    keys_collected, openrouter_last4 = _keys_collected_for(conn, session_id)

                    yield _sse_event(
                        "finish",
                        {
                            "user_message": message,
                            "assistant_html": render_chat_assistant_html(assistant_text),
                            "captured_count": sum(1 for name in ALLOWED_FILENAMES if name in captured),
                            "required_count": len(ALLOWED_FILENAMES),
                            "finalize_ready": all(name in captured for name in ALLOWED_FILENAMES),
                            "keys_collected": keys_collected,
                            "openrouter_last4": openrouter_last4,
                            "cumulative_cost_usd": cumulative_cost,
                        },
                    )

                elif chunk_type == "error":
                    saw_terminal = True
                    error_chunk = cast(StreamError, chunk)
                    translated = _translate(OpenRouterError(error_chunk["message"], kind=error_chunk["kind"]))
                    set_error(conn, session_id, translated.user_message)
                    conn.commit()
                    yield _sse_event(
                        "error",
                        {
                            **_kickoff_error_kind(was_kickoff, translated.kind),
                            "user_message": message,
                            "message": translated.user_message,
                        },
                    )
        except LLMSpendCeilingExceeded as e:
            # Defensive guard: complete_stream's internal _check_call_gate()
            # ran in a TOCTOU window after the route's gate passed. Yield an
            # SSE error event rather than letting the exception propagate
            # naked (which would close the response with no client signal).
            saw_terminal = True
            translated = _translate(e)
            yield _sse_event(
                "error",
                {
                    **_kickoff_error_kind(was_kickoff, translated.kind),
                    "user_message": message,
                    "message": translated.user_message,
                },
            )
    finally:
        # #743: log stream_cancelled iff complete_stream returned without a
        # terminal chunk AND the callback confirms cancellation was the cause.
        # Querying is_cancelled() (rather than just trusting `not saw_terminal`)
        # distinguishes cancellation from a hypothetical unexpected exception
        # exiting the generator — the latter shouldn't masquerade as a clean
        # cancel. Persistence (cost_log, append_turn, update_captured_blocks)
        # is automatically skipped either way because those calls live inside
        # the never-executed `finish` branch.
        if not saw_terminal and is_cancelled is not None and is_cancelled():
            log_event(
                "stream_cancelled",
                route="turn-stream",
                session_id=session_id,
                reason="client_disconnect",
            )
        conn.close()


@router.post("/onboarding/interview/turn-stream", response_model=None)
def post_turn_stream(
    request: Request,
    session_id: str = Form(...),
    message: str = Form(...),
) -> StreamingResponse | JSONResponse:
    """SSE streaming variant of ``POST /onboarding/interview/turn``.

    Returns an ``text/event-stream`` response that drives
    :func:`~findajob.llm.openrouter.complete_stream` and emits:

    - ``event: captured`` — one per file block close marker seen mid-stream.
    - ``event: finish`` — final event on success; carries pre-rendered
      ``assistant_html``, cost, and finalize-readiness fields.
    - ``event: error`` — on LLM error or ``finish_reason="length"``.

    Pre-flight checks (missing session, no key, spend ceiling) return
    non-streaming HTTP errors (404 / 503 / 402) BEFORE the SSE response
    is opened so the client can handle them as normal HTTP failures.
    """
    db_path: Path = request.app.state.db_path
    conn = connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        sess = get_session(conn, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")

        chat_key = _resolved_chat_key(conn, session_id)
        if not chat_key:
            raise _unavailable_503()

        # Spend ceiling gate — must run BEFORE opening the SSE response so the
        # client receives a normal 402 JSON response it can handle separately
        # from mid-stream SSE error events.
        try:
            check_call_gate()
        except LLMSpendCeilingExceeded as e:
            translated = _translate(e)
            return JSONResponse(
                status_code=402,
                content={"detail": translated.user_message},
            )

        # Snapshot session state — the generator runs AFTER this function
        # returns (StreamingResponse defers iteration) so we must pass in the
        # values now, not read them lazily from a closed connection.
        sess_history = list(sess.history)
        sess_captured = dict(sess.captured_blocks)
    finally:
        conn.close()

    # #743: callback reads the flag set by DisconnectStateMiddleware. Synchronous
    # read of scope dict — safe to call from the sync generator running inside
    # complete_stream's urllib loop. No threading, no async/sync impedance,
    # no race with Starlette's listen_for_disconnect (the middleware records
    # disconnect BEFORE that listener sees it).
    scope = request.scope

    def _client_disconnected() -> bool:
        return bool(scope.get(_DISCONNECT_SCOPE_KEY, False))

    return StreamingResponse(
        _stream_turn(
            db_path=db_path,
            session_id=session_id,
            chat_key=chat_key,
            message=message,
            sess_history=sess_history,
            sess_captured=sess_captured,
            is_cancelled=_client_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )


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
                gemini_api_key=(creds.gemini_api_key or "").strip(),
                conn=conn,
            )
        except OnboardingSmokeCheckFailed as e:
            # #631: 402 PaymentRequired gets its own status code + recovery
            # copy. Every other auth/throttle failure keeps the legacy 400 +
            # "change your key" framing.
            if e.status_code == 402:
                error_msg = (
                    f"{e.user_message} Once credits land, return here and "
                    "click Finalize again — no config files were written, "
                    "so the retry runs from a clean state."
                )
            else:
                error_msg = (
                    "OpenRouter rejected the key when we tried to verify it. "
                    f"{e.user_message} Use 'Change keys' on /onboarding/ to "
                    "supply a different key, then return here and click Finalize."
                )
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                keys_collected=keys_collected,
                openrouter_last4=openrouter_last4,
                cumulative_cost_usd=cumulative_cost,
                error=error_msg,
                status_code=e.status_code,
            )

        mark_complete(conn, session_id)
    finally:
        conn.close()

    # When the chosen adapter's env var is missing, gate to the feed-config
    # step where the user can enter the key and run a live test. From there
    # the user proceeds to the Gmail-config gate (#407), then to the
    # connections gate (#571), which is the terminal step that writes the
    # sentinel.
    #
    # When voice-samples LLM redaction failed during inject() (#634), append
    # ?voice_redact_failed=1 to the immediate redirect target. Both feed-config
    # and gmail-config GET handlers accept the param and render an amber warning
    # banner; the param is not propagated through /finish hops — one-shot
    # display on the page the user lands on immediately after Finalize.
    # Redirect to the spend-ceiling step (#671), which then makes the
    # feed-config vs gmail-config decision and redirects accordingly.
    # voice_redact_failed is propagated so spend-ceiling's /finish can
    # pass it to the immediate next page.
    redact_param = "?voice_redact_failed=1" if inject_result.voice_samples_redact_failed else ""
    return RedirectResponse(f"/onboarding/spend-ceiling/{session_id}/{redact_param}", status_code=303)
