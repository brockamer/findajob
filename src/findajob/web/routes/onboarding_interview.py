"""In-app onboarding interview routes (#336 Task 4).

Wires session_store + interview_runner + parser + injector into a chat
surface so non-technical testers can complete onboarding without leaving
findajob's UI. Conditionally registered in :func:`findajob.web.app.create_app`
only when ``OPENROUTER_OPERATOR_KEY`` is set — when unset, the in-app
interview is unavailable and ``/onboarding/`` falls back to paste-back only
(acceptance criterion #6 of #336).

Cross-task constraints (from #336 Session 2026-05-01):
- Emission detection runs against the cumulative assistant transcript on every
  turn, driven by :data:`findajob.onboarding.parser.ALLOWED_FILENAMES` (NEVER
  hardcoded counts) so #212 / #283 changes land cleanly.
- Finalize collects the user's OpenRouter key in a section structured to accept
  RapidAPI + Google fields later (#339).
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
    get_session,
    mark_complete,
    set_error,
    update_captured_blocks,
)

router = APIRouter()

OPERATOR_KEY_ENV = "OPENROUTER_OPERATOR_KEY"
_SYSTEM_PROMPT_RELPATH = Path("config") / "roles" / "onboarding_interviewer.md"
_KICKOFF_USER_MESSAGE = "Begin the interview."


def _operator_key() -> str:
    return (os.environ.get(OPERATOR_KEY_ENV) or "").strip()


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

    The router is registered only when ``OPENROUTER_OPERATOR_KEY`` is set, but
    we re-check here so the failure mode is a 503 rather than a 500 if env
    state shifts mid-process (and so this module is safe to import in tests
    that monkeypatch env after app construction).
    """
    operator_key = _operator_key()
    if not operator_key:
        raise HTTPException(status_code=503, detail="In-app interview unavailable on this deployment")

    conn = _conn(request)
    try:
        session_id = create_session(conn)
        try:
            assistant_text, _usage = run_turn(
                operator_key=operator_key,
                system_prompt=_system_prompt(request),
                history=[],
                user_message=_KICKOFF_USER_MESSAGE,
            )
        except InterviewRunnerError as e:
            set_error(conn, session_id, e.user_message)
            return _render_chat(
                request,
                session_id=session_id,
                history=[],
                captured={},
                error=e.user_message,
                status_code=502,
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
    operator_key = _operator_key()
    if not operator_key:
        raise HTTPException(status_code=503, detail="In-app interview unavailable on this deployment")

    conn = _conn(request)
    try:
        sess = get_session(conn, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")

        try:
            assistant_text, _usage = run_turn(
                operator_key=operator_key,
                system_prompt=_system_prompt(request),
                history=sess.history,
                user_message=message,
            )
        except InterviewRunnerError as e:
            set_error(conn, session_id, e.user_message)
            return _render_chat(
                request,
                session_id=session_id,
                history=sess.history,
                captured=sess.captured_blocks,
                error=e.user_message,
                status_code=502,
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
) -> HTMLResponse | RedirectResponse:
    """Validate captured blocks, run :func:`inject`, mark session complete."""
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

        if not openrouter_api_key.strip():
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
            inject_result = inject(base_root, sess.captured_blocks, openrouter_api_key=openrouter_api_key)
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
