"""End-to-end test for #743 client-disconnect → persistence-skip + log event.

Per the failure mode that surfaced in commit 228a6ef → fc6034c:
synthetic-fixture tests that stub complete_stream and trigger cancellation
directly cannot expose the production race condition. This test follows the
advisor's prescription: stub ONLY the LLM source (the urllib `resp` object
that complete_stream iterates); complete_stream itself, the production
DisconnectStateMiddleware, the route handler, and the SSE closure all run
unmocked.

Verification gap (per `feedback_disclose_verify_gaps`): this test drives
the ASGI app directly via asyncio.run, NOT through a real uvicorn process.
That means it confirms the middleware-flag-closure-callback chain works
when http.disconnect IS delivered, but it does NOT prove that uvicorn (in
production) actually delivers http.disconnect on a real client abort.
AC6's browser smoke is the canonical test for that final link.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from findajob.web.app import create_app
from findajob.web.middleware import SCOPE_KEY

_USER_KEY = "sk-or-v1-disconnect-test"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    """Standard onboarding test layout: candidate_context, config/roles, DB."""
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config" / "roles").mkdir(parents=True)
    (tmp_path / "logs").mkdir()

    repo_role = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"
    shutil.copy(repo_role, tmp_path / "config" / "roles" / "onboarding_interviewer.md")

    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(db_path)
    try:
        from findajob.db.migrate import apply_pending

        apply_pending(conn)
    finally:
        conn.close()
    return tmp_path


@pytest.fixture
def app(base_root: Path):
    """Production app: middleware + route in their real wiring."""
    return create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )


def _create_session(base_root: Path) -> str:
    """Insert a session row and set the instance OpenRouter key in the environment."""
    from findajob.onboarding.session_store import create_session

    os.environ["OPENROUTER_API_KEY"] = _USER_KEY
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
    finally:
        conn.close()
    return sid


def _build_sse_bytes(num_chunks: int = 20) -> bytes:
    """Build a slow-emitting SSE stream with `num_chunks` data lines + DONE."""
    lines: list[bytes] = []
    for i in range(num_chunks):
        chunk = {
            "id": f"gen-disconnect-{i}",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": f"chunk{i} ", "role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
        lines.append(f"data: {json.dumps(chunk)}\n\n".encode())
    # Final chunk with terminal finish_reason + usage. If complete_stream runs
    # all the way through (i.e., cancellation didn't fire), this would land as
    # the finish event.
    terminal = {
        "id": "gen-disconnect-final",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "", "role": "assistant"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 0},
            "cost": 0.0042,
        },
    }
    lines.append(f"data: {json.dumps(terminal)}\n\n".encode())
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


class _SlowSSEResponse:
    """Fake urllib response that yields one SSE line per __next__ with a small
    time.sleep between yields.

    The sleep is essential — the streaming generator runs in a threadpool via
    Starlette's iterate_in_threadpool, and the sleeps give the main asyncio
    loop room to run listen_for_disconnect between iterations. Without the
    sleeps the synchronous for-loop completes before the disconnect message
    propagates through the middleware.
    """

    def __init__(self, raw: bytes, *, per_line_sleep_s: float = 0.01) -> None:
        self._stream = io.BytesIO(raw)
        self.close_called = False
        self._sleep_s = per_line_sleep_s

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        line = self._stream.readline()
        if not line:
            raise StopIteration
        if self._sleep_s > 0:
            time.sleep(self._sleep_s)
        return line

    def close(self) -> None:
        self.close_called = True


def _make_form_body(session_id: str, message: str) -> bytes:
    """URL-encoded form body for POST /onboarding/interview/turn-stream."""
    from urllib.parse import urlencode

    return urlencode({"session_id": session_id, "message": message}).encode()


def _make_scope(method: str, path: str, body_len: int) -> dict[str, Any]:
    """Construct a minimal HTTP ASGI scope for the turn-stream route."""
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(body_len).encode()),
            (b"accept", b"text/event-stream"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }


def _make_receive_with_delayed_disconnect(body: bytes, delay_s: float = 0.3):
    """Receive that yields the body once, then delays before yielding http.disconnect.

    The delay is essential. Without it, listen_for_disconnect sees the disconnect
    BEFORE the streaming generator has a chance to start iterating — the task
    group cancels stream_response before any chunk is read, and complete_stream's
    `is_cancelled` polling never gets a chance to run. The delay mimics the
    real-world timing where chunks flow for a while before the client aborts.
    """
    state = {"body_sent": False}

    async def receive() -> dict[str, Any]:
        if not state["body_sent"]:
            state["body_sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Give the streaming generator time to start iterating urllib chunks.
        # listen_for_disconnect awaits this; the streaming task runs concurrently
        # during the sleep.
        await asyncio.sleep(delay_s)
        return {"type": "http.disconnect"}

    return receive


def _make_send_recorder() -> tuple[list[dict[str, Any]], Any]:
    """Send that collects all messages; returned along with the inspect list."""
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    return messages, send


# ── The test ──────────────────────────────────────────────────────────────────


def test_client_disconnect_skips_persistence_and_logs_cancellation(
    app, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full integration: ASGI app + middleware + route + complete_stream + urllib stub.

    Drives the ASGI app directly via asyncio.run rather than through TestClient,
    so we control the receive() callable and can deliver a real http.disconnect
    message to the production middleware. Only urllib is stubbed; everything
    else — DisconnectStateMiddleware, the route's scope-reading closure, and
    complete_stream's `is_cancelled` polling — runs real production code.
    """
    sid = _create_session(base_root)

    raw_sse = _build_sse_bytes(num_chunks=20)
    fake_resp = _SlowSSEResponse(raw_sse, per_line_sleep_s=0.01)

    # Record log_event calls so we can assert stream_cancelled fired.
    log_calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_log(event_type: str, **kwargs: object) -> None:
        log_calls.append((event_type, dict(kwargs)))

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.log_event", _fake_log)

    body = _make_form_body(sid, "Hello, interviewer.")
    scope = _make_scope("POST", "/onboarding/interview/turn-stream", len(body))
    receive = _make_receive_with_delayed_disconnect(body, delay_s=0.3)
    sent_messages, send = _make_send_recorder()

    async def _drive():
        # urllib stub installed only for the duration of the request.
        with patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=fake_resp,
        ):
            await app(scope, receive, send)
        # After the asyncio task is cancelled (listen_for_disconnect → task
        # group cancel), the sync streaming generator running in a threadpool
        # worker continues until it polls is_cancelled() on its next iteration
        # and early-returns. The generator's finally clause (which logs
        # stream_cancelled) runs in the worker thread after the await is
        # cancelled. We sleep briefly to let the worker thread complete its
        # cleanup before the test asserts.
        await asyncio.sleep(0.3)

    # Run the full ASGI request to completion.
    asyncio.run(_drive())

    # ── Assertions ────────────────────────────────────────────────────────────

    # 1. Response was opened (200 + text/event-stream) — the cancellation must
    # happen MID-STREAM, not before the response opens.
    start_msgs = [m for m in sent_messages if m["type"] == "http.response.start"]
    assert len(start_msgs) == 1, f"expected exactly one response.start, got {sent_messages}"
    assert start_msgs[0]["status"] == 200

    # 2. The middleware flipped the scope flag — confirms http.disconnect was
    # actually delivered through the chain.
    assert scope[SCOPE_KEY] is True, "DisconnectStateMiddleware should have observed http.disconnect"

    # 3. NO cost_log row written. This is the core economic assertion of #743 —
    # operator OpenRouter credit must not be charged for a cancelled turn.
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM cost_log WHERE operation = ?",
            ("onboarding_interviewer",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 0, "cost_log must NOT have a row for the cancelled turn"

    # 4. NO append_turn writes — chat history stays empty so the cancelled
    # assistant text doesn't show up on next page load.
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        history_row = conn.execute(
            "SELECT history_json FROM onboarding_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    assert history_row is not None
    history = json.loads(history_row[0]) if history_row[0] else []
    assert history == [], f"session history must be empty after cancellation, got {history}"

    # 5. stream_cancelled event was logged with the right metadata.
    cancelled_events = [(etype, kwargs) for etype, kwargs in log_calls if etype == "stream_cancelled"]
    assert len(cancelled_events) == 1, f"expected exactly one stream_cancelled log_event, got {log_calls}"
    _, cancel_kwargs = cancelled_events[0]
    assert cancel_kwargs.get("route") == "turn-stream"
    assert cancel_kwargs.get("reason") == "client_disconnect"
    assert cancel_kwargs.get("session_id") == sid

    # 6. urllib resp.close() was called via complete_stream's cancellation branch.
    assert fake_resp.close_called, "complete_stream's is_cancelled branch must call resp.close()"


def test_no_disconnect_completes_normally_writes_cost_log(
    app, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: without disconnect, the request completes normally —
    cost_log gets a row, history gets two turns, no stream_cancelled log.

    Guards against false-positive of test_client_disconnect_skips_*: if the
    test infrastructure spuriously skipped persistence regardless of
    cancellation, this test would fail.
    """
    sid = _create_session(base_root)

    # Small stream — no sleeps needed since we won't try to disconnect.
    raw_sse = _build_sse_bytes(num_chunks=3)
    fake_resp = _SlowSSEResponse(raw_sse, per_line_sleep_s=0.0)

    log_calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_log(event_type: str, **kwargs: object) -> None:
        log_calls.append((event_type, dict(kwargs)))

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.log_event", _fake_log)

    body = _make_form_body(sid, "Hello.")
    scope = _make_scope("POST", "/onboarding/interview/turn-stream", len(body))

    # Receive that yields body then BLOCKS indefinitely (never delivers disconnect)
    # — mimics a still-connected client.
    body_sent = [False]

    async def receive() -> dict[str, Any]:
        if not body_sent[0]:
            body_sent[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Never delivers disconnect — wait forever. The response completes
        # via the normal finish-chunk path; listen_for_disconnect's pending
        # await is cancelled by Starlette when the response finishes.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}  # unreachable

    sent_messages, send = _make_send_recorder()

    async def _drive():
        with patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=fake_resp,
        ):
            await app(scope, receive, send)

    asyncio.run(_drive())

    # Response completed normally.
    start_msgs = [m for m in sent_messages if m["type"] == "http.response.start"]
    assert len(start_msgs) == 1
    assert start_msgs[0]["status"] == 200

    # Flag stayed False — no disconnect was injected.
    assert scope[SCOPE_KEY] is False

    # cost_log got a row.
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(cost_usd) FROM cost_log WHERE operation = ?",
            ("onboarding_interviewer",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 1
    assert row[1] is not None and row[1] > 0

    # History has both turns.
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        history_row = conn.execute(
            "SELECT history_json FROM onboarding_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    history = json.loads(history_row[0])
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"

    # No stream_cancelled log.
    cancelled = [etype for etype, _ in log_calls if etype == "stream_cancelled"]
    assert cancelled == [], f"no stream_cancelled expected on normal completion, got {log_calls}"
