"""Tests for POST /onboarding/interview/turn-stream (#740).

Covers the SSE streaming route that drives complete_stream() and re-emits
chunks as Server-Sent Events. The non-streaming /turn route is tested
separately in test_web_onboarding_interview_routes.py.

All LLM calls are stubbed via monkeypatch so no real HTTP occurs.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from findajob.llm.openrouter import (
    LLMSpendCeilingExceeded,
    StreamCaptured,
    StreamError,
    StreamFinish,
    StreamUsage,
)
from findajob.web.app import create_app

_USER_KEY = "sk-or-v1-stream-test"

# Minimal emission that _captured_from_history() will pick up.
# Must use a name from ALLOWED_FILENAMES — parse_emission ignores unknown names.
_FILE_NAME = "profile.md"
_EMISSION_TEXT = f"<<<FILE: {_FILE_NAME}>>>\nsome profile content\n<<<END FILE: {_FILE_NAME}>>>"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config" / "roles").mkdir(parents=True)
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
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _create_session(base_root: Path, *, openrouter: str = _USER_KEY) -> str:
    """Insert a session row with credentials bound to it."""
    from findajob.onboarding.session_store import create_session, set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
        set_credentials(conn, sid, openrouter_api_key=openrouter, rapidapi_key="")
    finally:
        conn.close()
    return sid


def _read_session_row(base_root: Path, session_id: str) -> dict[str, Any]:
    """Return full session row as dict."""
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM onboarding_sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"session {session_id!r} not found"
    return dict(row)


def _make_usage(cost_usd: float = 0.001) -> StreamUsage:
    return StreamUsage(
        prompt_tokens=10,
        completion_tokens=20,
        cached_tokens=0,
        cost_usd=cost_usd,
    )


def _stub_complete_stream(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list,
) -> None:
    """Replace complete_stream in the route module with a fake that yields chunks."""

    def _fake(**_kwargs):
        yield from chunks

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.complete_stream", _fake)


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse raw SSE body into list of {event, data} dicts."""
    events: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    for line in body.splitlines():
        if line.startswith("event: "):
            current["event"] = line[len("event: ") :]
        elif line.startswith("data: "):
            current["data"] = line[len("data: ") :]
        elif line == "" and current:
            events.append(
                {
                    "event": current.get("event", ""),
                    "data": json.loads(current.get("data", "{}")),
                }
            )
            current = {}
    return events


def _post_stream(client: TestClient, session_id: str, message: str = "hello"):
    return client.post(
        "/onboarding/interview/turn-stream",
        data={"session_id": session_id, "message": message},
    )


# ── Test 1: 404 on missing session ────────────────────────────────────────────


def test_404_on_missing_session(client: TestClient) -> None:
    """POST to /turn-stream with unknown session_id → 404 (not streaming)."""
    resp = _post_stream(client, "nonexistent-session-id")
    assert resp.status_code == 404
    # Must be a JSON/HTML response, NOT text/event-stream
    assert "text/event-stream" not in resp.headers.get("content-type", "")


# ── Test 2: 503 on missing key ────────────────────────────────────────────────


def test_503_on_missing_key(client: TestClient, base_root: Path) -> None:
    """Session with no OpenRouter key → 503 (not streaming)."""
    from findajob.onboarding.session_store import create_session

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
        # No set_credentials call — no key on file
    finally:
        conn.close()

    resp = _post_stream(client, sid)
    assert resp.status_code == 503
    assert "text/event-stream" not in resp.headers.get("content-type", "")


# ── Test 3: Happy path captured + finish events ───────────────────────────────


def test_happy_path_emits_captured_and_finish(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """complete_stream yields captured + finish → response has both SSE events."""
    sid = _create_session(base_root)
    _stub_complete_stream(
        monkeypatch,
        [
            StreamCaptured(type="captured", name="voice_samples_a.md"),
            StreamFinish(
                type="finish",
                text="hello",
                finish_reason="stop",
                usage=_make_usage(),
                generation_id="gen-1",
            ),
        ],
    )

    resp = _post_stream(client, sid)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert "captured" in types
    assert "finish" in types
    assert "error" not in types

    captured_ev = next(e for e in events if e["event"] == "captured")
    assert captured_ev["data"]["name"] == "voice_samples_a.md"

    finish_ev = next(e for e in events if e["event"] == "finish")
    assert "assistant_html" in finish_ev["data"]
    assert "user_message" in finish_ev["data"]
    assert "cumulative_cost_usd" in finish_ev["data"]
    assert "finalize_ready" in finish_ev["data"]
    assert "keys_collected" in finish_ev["data"]
    assert "openrouter_last4" in finish_ev["data"]


# ── Test 4: cost_log written on finish ────────────────────────────────────────


def test_cost_log_written_on_finish(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After streaming, cost_log has a row for onboarding_interviewer."""
    sid = _create_session(base_root)
    _stub_complete_stream(
        monkeypatch,
        [
            StreamFinish(
                type="finish",
                text="test reply",
                finish_reason="stop",
                usage=_make_usage(cost_usd=0.0042),
                generation_id="gen-cost",
            ),
        ],
    )

    _post_stream(client, sid)

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT operation, cost_usd FROM cost_log WHERE operation = ?",
            ("onboarding_interviewer",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "onboarding_interviewer"
    assert row[1] > 0


# ── Test 5: append_turn writes both user + assistant ─────────────────────────


def test_append_turn_writes_both_turns(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After streaming, session history has user + assistant turns."""
    sid = _create_session(base_root)
    _stub_complete_stream(
        monkeypatch,
        [
            StreamFinish(
                type="finish",
                text="assistant reply",
                finish_reason="stop",
                usage=_make_usage(),
                generation_id=None,
            ),
        ],
    )

    _post_stream(client, sid, message="user input")

    row = _read_session_row(base_root, sid)
    history = json.loads(row["history_json"])
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "user input"}
    assert history[1] == {"role": "assistant", "content": "assistant reply"}


# ── Test 6: captured_blocks updated ──────────────────────────────────────────


def test_captured_blocks_updated(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Assistant text with FILE block → captured_blocks updated in DB."""
    sid = _create_session(base_root)
    _stub_complete_stream(
        monkeypatch,
        [
            StreamFinish(
                type="finish",
                text=_EMISSION_TEXT,
                finish_reason="stop",
                usage=_make_usage(),
                generation_id=None,
            ),
        ],
    )

    _post_stream(client, sid)

    row = _read_session_row(base_root, sid)
    captured = json.loads(row["captured_blocks_json"])
    assert _FILE_NAME in captured


# ── Test 7: finish_reason="length" emits error, not finish ───────────────────


def test_length_finish_reason_emits_error(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """finish_reason='length' → SSE error event, no append_turn."""
    sid = _create_session(base_root)
    _stub_complete_stream(
        monkeypatch,
        [
            StreamFinish(
                type="finish",
                text="truncated...",
                finish_reason="length",
                usage=_make_usage(),
                generation_id=None,
            ),
        ],
    )

    resp = _post_stream(client, sid)
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert "error" in types
    assert "finish" not in types

    error_ev = next(e for e in events if e["event"] == "error")
    assert error_ev["data"]["kind"] == "length"
    assert "max_tokens" in error_ev["data"]["message"].lower() or "trim" in error_ev["data"]["message"].lower()

    # History must be unchanged — no append_turn called
    row = _read_session_row(base_root, sid)
    history = json.loads(row["history_json"])
    assert history == []


# ── Test 8: Mid-stream error passes through ───────────────────────────────────


def test_mid_stream_error_passes_through(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """captured + StreamError → both events emitted; no append_turn."""
    sid = _create_session(base_root)
    _stub_complete_stream(
        monkeypatch,
        [
            StreamCaptured(type="captured", name="voice_samples_a.md"),
            StreamError(type="error", kind="network", message="connection reset"),
        ],
    )

    resp = _post_stream(client, sid)
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert "captured" in types
    assert "error" in types
    assert "finish" not in types

    # No turns persisted
    row = _read_session_row(base_root, sid)
    history = json.loads(row["history_json"])
    assert history == []

    # error_state was set
    assert row["error_state"] is not None


# ── Test 9: LLMSpendCeilingExceeded → 402 (not SSE) ─────────────────────────


def test_spend_ceiling_returns_402(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check_call_gate raises LLMSpendCeilingExceeded → 402 JSON, not streaming."""
    sid = _create_session(base_root)

    def _fake_gate():
        raise LLMSpendCeilingExceeded(ceiling_usd=10.0, current_sum_usd=10.5)

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.check_call_gate", _fake_gate)

    resp = _post_stream(client, sid)
    assert resp.status_code == 402
    # Must NOT be SSE
    assert "text/event-stream" not in resp.headers.get("content-type", "")
    body = resp.json()
    assert "detail" in body


# ── Test 10: Response headers ─────────────────────────────────────────────────


def test_response_headers(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Streaming response has correct content-type and cache headers."""
    sid = _create_session(base_root)
    _stub_complete_stream(
        monkeypatch,
        [
            StreamFinish(
                type="finish",
                text="hi",
                finish_reason="stop",
                usage=_make_usage(),
                generation_id=None,
            ),
        ],
    )

    resp = _post_stream(client, sid)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("x-accel-buffering") == "no"
    assert resp.headers.get("cache-control") == "no-cache"


# ── Test 11: clear_error called on success ────────────────────────────────────


def test_clear_error_called_on_success(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-existing error_state is cleared when a turn succeeds."""
    sid = _create_session(base_root)

    # Plant a non-NULL error_state on the session
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        conn.execute(
            "UPDATE onboarding_sessions SET error_state = ? WHERE id = ?",
            ("prior error", sid),
        )
        conn.commit()
    finally:
        conn.close()

    _stub_complete_stream(
        monkeypatch,
        [
            StreamFinish(
                type="finish",
                text="all good now",
                finish_reason="stop",
                usage=_make_usage(),
                generation_id=None,
            ),
        ],
    )

    _post_stream(client, sid)

    row = _read_session_row(base_root, sid)
    assert row["error_state"] is None
