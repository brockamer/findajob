"""Integration tests for /onboarding/interview/* routes (#336 Task 4).

The four routes wire session_store + interview_runner + parser + injector
into a chat surface. Tests mock the OpenRouter call (interview_runner.run_turn)
and the OpenRouter smoke check, then assert on observable state: session
rows, captured_blocks, redirects, error_state.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding.interview_runner import InterviewRunnerError
from findajob.web.app import create_app

# Schema covers the three tables the route module + the onboarding guard
# touch: jobs/audit_log (used by the guard's no-op-OK path under TestClient)
# and onboarding_sessions (the route's read/write target).
_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    created_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE onboarding_sessions (
    id TEXT PRIMARY KEY,
    history_json TEXT NOT NULL,
    captured_blocks_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    last_turn_at TEXT NOT NULL,
    completed_at TEXT,
    error_state TEXT
);
"""

_OPERATOR_KEY = "sk-or-v1-operator-test"
_USER_KEY = "sk-or-v1-user-test"


def _build_emission_blob() -> str:
    """A complete emission covering every ALLOWED_FILENAMES entry.

    Used to test the finalize path. Each block body is minimal but parses.
    """
    from findajob.onboarding.parser import ALLOWED_FILENAMES

    parts = []
    for name in ALLOWED_FILENAMES:
        parts.append(f"<<<FILE: {name}>>>\nbody for {name}\n<<<END FILE: {name}>>>")
    return "\n\n".join(parts)


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
    conn.executescript(_SCHEMA)
    conn.close()
    return tmp_path


@pytest.fixture
def client_with_key(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENROUTER_OPERATOR_KEY", _OPERATOR_KEY)
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client_no_key(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("OPENROUTER_OPERATOR_KEY", raising=False)
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _stub_run_turn(monkeypatch: pytest.MonkeyPatch, assistant_text: str) -> list[dict[str, Any]]:
    """Replace run_turn with a stub that records calls and returns a fixed reply."""
    calls: list[dict[str, Any]] = []

    def _fake(operator_key: str, system_prompt: str, history: list, user_message: str):
        calls.append(
            {
                "operator_key": operator_key,
                "system_prompt": system_prompt,
                "history": list(history),
                "user_message": user_message,
            }
        )
        return assistant_text, {"prompt_tokens": 10, "completion_tokens": 20}

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.run_turn", _fake)
    return calls


def _stub_run_turn_error(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    def _fake(*_args, **_kwargs):
        raise InterviewRunnerError(message)

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.run_turn", _fake)


def _read_session(base_root: Path, session_id: str) -> tuple[str, str, str | None, str | None]:
    """Return (history_json, captured_blocks_json, completed_at, error_state)."""
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT history_json, captured_blocks_json, completed_at, error_state "
            "FROM onboarding_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"session {session_id} not found"
    return row


# ── Conditional registration ──────────────────────────────────────────────


def test_router_not_registered_when_operator_key_unset(client_no_key: TestClient) -> None:
    """Acceptance #6: when OPENROUTER_OPERATOR_KEY is unset, the in-app
    interview is unavailable — routes return 404 (not registered)."""
    resp = client_no_key.post("/onboarding/interview/start")
    assert resp.status_code == 404


def test_paste_back_path_still_available_when_operator_key_unset(client_no_key: TestClient) -> None:
    """Acceptance #6 negative side: existing paste-back must keep working."""
    resp = client_no_key.get("/onboarding/")
    assert resp.status_code == 200
    assert 'name="emission"' in resp.text  # paste form survives


def test_router_registered_when_operator_key_set(client_with_key: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_run_turn(monkeypatch, "hello — what's your full name?")
    resp = client_with_key.post("/onboarding/interview/start")
    # Whatever the response, it MUST NOT be 404 (router not registered).
    assert resp.status_code != 404


# ── /start ────────────────────────────────────────────────────────────────


def test_start_creates_session_and_runs_first_turn(
    client_with_key: TestClient,
    base_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_run_turn(monkeypatch, "hello — what's your full name?")
    resp = client_with_key.post("/onboarding/interview/start")

    assert resp.status_code == 303  # redirect to GET /interview/{sid}
    location = resp.headers["location"]
    assert location.startswith("/onboarding/interview/")
    sid = location.rsplit("/", 1)[-1]

    # run_turn was called exactly once with the operator key + non-empty system prompt + empty history
    assert len(calls) == 1
    assert calls[0]["operator_key"] == _OPERATOR_KEY
    assert "interviewer" in calls[0]["system_prompt"].lower() or len(calls[0]["system_prompt"]) > 100
    assert calls[0]["history"] == []
    assert calls[0]["user_message"]  # synthetic kickoff non-empty

    # Session row has both turns persisted
    history_json, _captured_json, completed_at, error_state = _read_session(base_root, sid)
    import json as _json

    history = _json.loads(history_json)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "hello — what's your full name?"
    assert completed_at is None
    assert error_state is None


def test_start_handles_runner_error(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_run_turn_error(monkeypatch, "OpenRouter rejected the operator key (401 Unauthorized).")
    resp = client_with_key.post("/onboarding/interview/start")

    # Error responses render in-page rather than redirecting (so the user sees the banner)
    assert resp.status_code in (200, 502)
    assert "401" in resp.text or "OpenRouter" in resp.text

    # Session was created and error_state captured (so the operator can debug from the DB)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT id, error_state FROM onboarding_sessions ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[1] is not None
    assert "401" in row[1] or "OpenRouter" in row[1]


# ── /turn ─────────────────────────────────────────────────────────────────


def _create_session_directly(base_root: Path) -> str:
    """Insert a session row directly so /turn tests don't depend on /start."""
    from findajob.onboarding.session_store import create_session

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
    finally:
        conn.close()
    return sid


def test_turn_appends_pair_and_returns_assistant_partial(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _create_session_directly(base_root)
    _stub_run_turn(monkeypatch, "got it — and your timezone?")

    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "Test User"},
    )
    assert resp.status_code == 200
    # The assistant turn body should appear in the response (partial render)
    assert "got it — and your timezone?" in resp.text

    history_json, _captured, _completed, _err = _read_session(base_root, sid)
    import json as _json

    history = _json.loads(history_json)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Test User"}
    assert history[1] == {"role": "assistant", "content": "got it — and your timezone?"}


def test_turn_passes_persisted_history_to_run_turn(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-turn safety: each /turn call must ship the full prior history."""
    from findajob.onboarding.session_store import append_turn

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        append_turn(conn, sid, "user", "kickoff")
        append_turn(conn, sid, "assistant", "first question?")
    finally:
        conn.close()

    calls = _stub_run_turn(monkeypatch, "next question?")
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "first answer"},
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    history_sent = calls[0]["history"]
    # The prior two turns must have been included; the new user message is in user_message
    assert {"role": "user", "content": "kickoff"} in history_sent
    assert {"role": "assistant", "content": "first question?"} in history_sent
    assert calls[0]["user_message"] == "first answer"


def test_turn_detects_emission_blocks_on_assistant_message(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the assistant emits a `<<<FILE: ...>>>` block, captured_blocks updates."""
    sid = _create_session_directly(base_root)
    assistant_with_block = (
        "Great — here is your profile:\n\n"
        "<<<FILE: profile.md>>>\nname: Test\n<<<END FILE: profile.md>>>\n\n"
        "Now let's continue."
    )
    _stub_run_turn(monkeypatch, assistant_with_block)

    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "ready"},
    )
    assert resp.status_code == 200

    _hist, captured_json, _completed, _err = _read_session(base_root, sid)
    import json as _json

    captured = _json.loads(captured_json)
    assert "profile.md" in captured
    assert "name: Test" in captured["profile.md"]


def test_turn_handles_runner_error(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(monkeypatch, "OpenRouter rate-limited the request (429).")

    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "anything"},
    )
    assert resp.status_code in (200, 502, 429)
    assert "429" in resp.text or "rate" in resp.text.lower()


def test_turn_404_for_unknown_session(client_with_key: TestClient) -> None:
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": "does-not-exist", "message": "hi"},
    )
    assert resp.status_code == 404


# ── GET /{session_id} (resume) ────────────────────────────────────────────


def test_resume_renders_persisted_history(client_with_key: TestClient, base_root: Path) -> None:
    from findajob.onboarding.session_store import append_turn

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        append_turn(conn, sid, "user", "first user msg with marker MARK_USER")
        append_turn(conn, sid, "assistant", "first assistant reply with marker MARK_ASSISTANT")
    finally:
        conn.close()

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    assert "MARK_USER" in resp.text
    assert "MARK_ASSISTANT" in resp.text


def test_resume_404_for_unknown_session(client_with_key: TestClient) -> None:
    resp = client_with_key.get("/onboarding/interview/nope")
    assert resp.status_code == 404


# ── /finalize ─────────────────────────────────────────────────────────────


def test_finalize_rejects_when_blocks_missing(client_with_key: TestClient, base_root: Path) -> None:
    """captured_blocks must contain every ALLOWED_FILENAMES entry before finalize."""
    sid = _create_session_directly(base_root)

    resp = client_with_key.post(
        f"/onboarding/interview/{sid}/finalize",
        data={"openrouter_api_key": _USER_KEY},
    )
    assert resp.status_code == 400
    assert "missing" in resp.text.lower() or "complete" in resp.text.lower()


def test_finalize_rejects_when_user_key_blank(client_with_key: TestClient, base_root: Path) -> None:
    """Even if all blocks captured, a blank user OpenRouter key is rejected."""
    from findajob.onboarding.parser import parse_emission
    from findajob.onboarding.session_store import update_captured_blocks

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        all_captured = parse_emission(_build_emission_blob()).found
        update_captured_blocks(conn, sid, all_captured)
    finally:
        conn.close()

    resp = client_with_key.post(
        f"/onboarding/interview/{sid}/finalize",
        data={"openrouter_api_key": "  "},
    )
    assert resp.status_code == 400
    assert "key" in resp.text.lower()


def test_finalize_calls_inject_and_marks_complete(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: all captured + valid key → inject() called → mark_complete + redirect."""
    from findajob.onboarding.injector import DiscoveryStatus, InjectResult
    from findajob.onboarding.parser import parse_emission
    from findajob.onboarding.session_store import update_captured_blocks

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        all_captured = parse_emission(_build_emission_blob()).found
        update_captured_blocks(conn, sid, all_captured)
    finally:
        conn.close()

    inject_calls: list[dict[str, Any]] = []

    def _fake_inject(base_root, parsed_files, *, openrouter_api_key):  # type: ignore[no-untyped-def]
        inject_calls.append(
            {
                "base_root": base_root,
                "parsed_files": dict(parsed_files),
                "openrouter_api_key": openrouter_api_key,
            }
        )
        return InjectResult(
            backup_dir=Path("/tmp/fake-backup"),
            discovery=DiscoveryStatus(success=True, count=3, error=None),
        )

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.inject", _fake_inject)

    resp = client_with_key.post(
        f"/onboarding/interview/{sid}/finalize",
        data={"openrouter_api_key": _USER_KEY},
    )

    # Either renders complete page directly (200) or redirects to /onboarding/complete
    assert resp.status_code in (200, 303)
    if resp.status_code == 303:
        assert "/onboarding/complete" in resp.headers["location"]

    # inject was called with the captured blocks + the user's key
    assert len(inject_calls) == 1
    assert inject_calls[0]["openrouter_api_key"] == _USER_KEY
    assert set(inject_calls[0]["parsed_files"].keys()) >= set(all_captured.keys())

    # Session marked complete
    _hist, _cap, completed_at, _err = _read_session(base_root, sid)
    assert completed_at is not None


def test_finalize_handles_smoke_check_failed(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If inject() raises OnboardingSmokeCheckFailed, render the friendly error
    so the user can fix their key and retry. Session is NOT marked complete."""
    from findajob.onboarding.openrouter_smoke import OnboardingSmokeCheckFailed
    from findajob.onboarding.parser import parse_emission
    from findajob.onboarding.session_store import update_captured_blocks

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        all_captured = parse_emission(_build_emission_blob()).found
        update_captured_blocks(conn, sid, all_captured)
    finally:
        conn.close()

    def _fake_inject(*_args, **_kwargs):
        raise OnboardingSmokeCheckFailed("OpenRouter rejected the key (401).")

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.inject", _fake_inject)

    resp = client_with_key.post(
        f"/onboarding/interview/{sid}/finalize",
        data={"openrouter_api_key": _USER_KEY},
    )
    assert resp.status_code == 400
    assert "401" in resp.text or "key" in resp.text.lower()

    _hist, _cap, completed_at, _err = _read_session(base_root, sid)
    assert completed_at is None  # NOT marked complete on smoke-check failure


def test_finalize_404_for_unknown_session(client_with_key: TestClient) -> None:
    resp = client_with_key.post(
        "/onboarding/interview/nope/finalize",
        data={"openrouter_api_key": _USER_KEY},
    )
    assert resp.status_code == 404
