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


def _plant_credentials(
    base_root: Path,
    *,
    openrouter: str = _USER_KEY,
    rapidapi: str = "",
    google: str = "",
) -> str:
    """Insert a credentials-only session row directly via session_store.

    Used by tests that need /start to find a credentials-only row to
    promote, without going through the full /onboarding/keys POST cycle.
    Returns the session id (in case the test needs it).
    """
    from findajob.onboarding.session_store import create_session, set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
        set_credentials(
            conn,
            sid,
            openrouter_api_key=openrouter,
            rapidapi_key=rapidapi,
            google_api_key=google,
        )
    finally:
        conn.close()
    return sid


def _set_credentials_on_session(base_root: Path, session_id: str, *, openrouter: str = _USER_KEY) -> None:
    """Bind credentials to an existing session — used by /finalize tests."""
    from findajob.onboarding.session_store import set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        set_credentials(
            conn,
            session_id,
            openrouter_api_key=openrouter,
            rapidapi_key="",
            google_api_key="",
        )
    finally:
        conn.close()


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


def _stub_run_turn_error(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    *,
    kind: str = "unknown",
    status_code: int | None = None,
) -> None:
    def _fake(*_args, **_kwargs):
        raise InterviewRunnerError(message, kind=kind, status_code=status_code)

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


def test_routes_register_but_503_when_no_chat_key_resolved(client_no_key: TestClient) -> None:
    """#339 changed gating from import-time to per-request.

    With no OPENROUTER_OPERATOR_KEY env AND no tester credentials collected
    via /onboarding/ Step 1, the route is registered (no 404) but resolves
    to a 503 with a pointer back to /onboarding/. This is the self-deploy
    failure mode — the previous import-time 404 was the wrong shape because
    a self-deploy stack with tester credentials NEEDS the route to register.
    """
    resp = client_no_key.post("/onboarding/interview/start")
    assert resp.status_code == 503
    assert "onboarding" in resp.text.lower()


def test_start_uses_tester_credentials_when_no_operator_env(
    client_no_key: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    base_root: Path,
) -> None:
    """Self-deploy: no operator env, tester credentials collected — chat runs
    on the tester's own OpenRouter key."""
    _plant_credentials(base_root, openrouter="sk-or-v1-tester-self-deploy")

    calls = _stub_run_turn(monkeypatch, "Hi! What's your name?")
    resp = client_no_key.post("/onboarding/interview/start")
    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["operator_key"] == "sk-or-v1-tester-self-deploy"


def test_start_503_when_no_credentials_collected(
    client_with_key: TestClient,
) -> None:
    """Step 1 (API-key collection) is mandatory before in-app interview can
    start — even when OPENROUTER_OPERATOR_KEY is set. Operator key only
    subsidizes the chat runner; it doesn't substitute for Step 1."""
    resp = client_with_key.post("/onboarding/interview/start")
    assert resp.status_code == 503
    assert "onboarding" in resp.text.lower()


def test_start_operator_key_wins_when_set(
    client_with_key: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    base_root: Path,
) -> None:
    """Operator-key precedence (flipped 2026-05-02): when
    OPENROUTER_OPERATOR_KEY is set on the stack, chat runs on the operator's
    key even when the tester collected their own key. This makes findajob-test
    a free dogfood instance — the tester's pipeline still uses their own key
    via /finalize, but the chat is subsidized."""
    _plant_credentials(base_root, openrouter="sk-or-v1-tester-priority")

    calls = _stub_run_turn(monkeypatch, "Hi!")
    resp = client_with_key.post("/onboarding/interview/start")
    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["operator_key"] == _OPERATOR_KEY


def test_router_registered_when_operator_key_set(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _plant_credentials(base_root)
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
    _plant_credentials(base_root)
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
    _plant_credentials(base_root)
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


def test_finalize_rejects_when_session_has_no_credentials(client_with_key: TestClient, base_root: Path) -> None:
    """Finalize requires credentials bound to the session — those come from
    Step 1 of /onboarding/. The earlier "blank form input" path was retired
    along with the form OR-key field on 2026-05-02."""
    from findajob.onboarding.parser import parse_emission
    from findajob.onboarding.session_store import update_captured_blocks

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        all_captured = parse_emission(_build_emission_blob()).found
        update_captured_blocks(conn, sid, all_captured)
    finally:
        conn.close()
    # Note: NOT calling _set_credentials_on_session — leaving it bare.

    resp = client_with_key.post(f"/onboarding/interview/{sid}/finalize")
    assert resp.status_code == 400
    body = resp.text.lower()
    assert "step 1" in body or "key" in body


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

    def _fake_inject(  # type: ignore[no-untyped-def]
        base_root,
        parsed_files,
        *,
        openrouter_api_key,
        rapidapi_key="",
        google_api_key="",
    ):
        inject_calls.append(
            {
                "base_root": base_root,
                "parsed_files": dict(parsed_files),
                "openrouter_api_key": openrouter_api_key,
                "rapidapi_key": rapidapi_key,
                "google_api_key": google_api_key,
            }
        )
        return InjectResult(
            backup_dir=Path("/tmp/fake-backup"),
            discovery=DiscoveryStatus(success=True, count=3, error=None),
        )

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.inject", _fake_inject)
    _set_credentials_on_session(base_root, sid, openrouter=_USER_KEY)

    resp = client_with_key.post(f"/onboarding/interview/{sid}/finalize")

    # Either renders complete page directly (200) or redirects to /onboarding/complete
    assert resp.status_code in (200, 303)
    if resp.status_code == 303:
        assert "/onboarding/complete" in resp.headers["location"]

    # inject was called with the captured blocks + the user's key (from creds)
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
    _set_credentials_on_session(base_root, sid, openrouter=_USER_KEY)

    resp = client_with_key.post(f"/onboarding/interview/{sid}/finalize")
    assert resp.status_code == 400
    assert "401" in resp.text or "key" in resp.text.lower()

    _hist, _cap, completed_at, _err = _read_session(base_root, sid)
    assert completed_at is None  # NOT marked complete on smoke-check failure


def test_finalize_404_for_unknown_session(client_with_key: TestClient) -> None:
    resp = client_with_key.post("/onboarding/interview/nope/finalize")
    assert resp.status_code == 404


# ── Multi-turn emission tracking (#336 Task 6) ────────────────────────────


def test_emission_split_across_turns_captures_after_close(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `<<<FILE: ...>>> ... <<<END FILE: ...>>>` block whose markers
    straddle two assistant turns must be captured.

    LLMs sometimes split blocks across turns mid-stream. The route's
    `_captured_from_history` joins assistant turns with `\\n\\n` and re-runs
    `parse_emission` over the full transcript so the regex can match
    across turn boundaries — this test pins that behavior.
    """
    sid = _create_session_directly(base_root)

    # Turn 1: open marker only — parse_emission on this single turn returns nothing
    turn1_text = "Here is your profile, splitting across turns:\n<<<FILE: profile.md>>>\nname: Test\nrole: alpha"
    # Turn 2: close marker only
    turn2_text = "second half of the body\n<<<END FILE: profile.md>>>\nNext question?"

    # First /turn — captured_blocks stays empty
    monkeypatch.setattr(
        "findajob.web.routes.onboarding_interview.run_turn",
        lambda *a, **kw: (turn1_text, {}),
    )
    resp1 = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "ready for first half"},
    )
    assert resp1.status_code == 200
    _h, captured_json_after_turn1, _c, _e = _read_session(base_root, sid)
    import json as _json

    assert _json.loads(captured_json_after_turn1) == {}

    # Second /turn — cumulative-transcript parse now finds the complete block
    monkeypatch.setattr(
        "findajob.web.routes.onboarding_interview.run_turn",
        lambda *a, **kw: (turn2_text, {}),
    )
    resp2 = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "ready for second half"},
    )
    assert resp2.status_code == 200

    _h, captured_json_after_turn2, _c, _e = _read_session(base_root, sid)
    captured = _json.loads(captured_json_after_turn2)
    assert "profile.md" in captured
    body = captured["profile.md"]
    assert "name: Test" in body
    assert "second half of the body" in body


# ── Error UX (#336 Task 7) ────────────────────────────────────────────────


def test_error_turn_renders_partial_not_full_page(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/turn` errors return an HTMX-friendly partial (no <html> wrapper)
    so the chat surface keeps the prior history and just appends an
    error bubble. /start errors render the full page (no chat to splice
    into yet)."""
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(monkeypatch, "boom", kind="upstream", status_code=503)

    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "any"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Partial — no full-document wrappers
    assert "<html" not in body.lower()
    assert "<body" not in body.lower()
    # Error bubble present, distinguishable in the DOM
    assert 'data-role="error"' in body
    assert 'data-error-kind="upstream"' in body


def test_error_kind_auth_surfaces_openrouter_keys_link(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401 → keys page link (operator-side fix), distinct from per-tester
    smoke-check finalize message which references the user's own key."""
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(
        monkeypatch,
        "OpenRouter rejected the operator key (401 Unauthorized).",
        kind="auth",
        status_code=401,
    )
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "any"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert 'data-error-kind="auth"' in body
    assert "openrouter.ai/keys" in body
    assert "401" in body


def test_error_kind_payment_surfaces_credits_link(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """402 → credits page link."""
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(
        monkeypatch,
        "Operator's OpenRouter account is out of credit (402).",
        kind="payment",
        status_code=402,
    )
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "any"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert 'data-error-kind="payment"' in body
    assert "openrouter.ai/credits" in body


def test_error_kind_rate_limit_includes_auto_retry_hint(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """429 → countdown hint + Try Again button (manual override)."""
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(
        monkeypatch,
        "OpenRouter rate-limited the request (429).",
        kind="rate_limit",
        status_code=429,
    )
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "any"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert 'data-error-kind="rate_limit"' in body
    assert "data-auto-retry-seconds" in body
    # Manual retry affordance still present so the user can override the wait
    assert "/onboarding/interview/turn" in body  # retry form posts here


def test_error_kind_upstream_renders_status_code(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5xx → render the specific status code so the operator can grep logs."""
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(
        monkeypatch,
        "OpenRouter or the upstream model returned a server error (504).",
        kind="upstream",
        status_code=504,
    )
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "any"},
    )
    body = resp.text
    assert 'data-error-kind="upstream"' in body
    assert "504" in body


def test_error_kind_network_renders_friendly_message(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URLError → kind=network, no OpenRouter dashboard link."""
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(
        monkeypatch,
        "Could not reach OpenRouter (timed out). Check the deployment's network connectivity.",
        kind="network",
    )
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "any"},
    )
    body = resp.text
    assert 'data-error-kind="network"' in body
    assert "openrouter.ai/keys" not in body  # no dashboard link for network errors
    assert "openrouter.ai/credits" not in body


def test_error_retry_replays_user_message(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Try Again form must carry the user's just-sent message so a
    retry replays the same turn rather than losing input."""
    sid = _create_session_directly(base_root)
    _stub_run_turn_error(
        monkeypatch,
        "OpenRouter rate-limited (429).",
        kind="rate_limit",
        status_code=429,
    )
    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "the user's important reply"},
    )
    body = resp.text
    # The hidden input replays the original message
    assert 'name="message"' in body
    assert "the user&#39;s important reply" in body or "the user's important reply" in body


def test_error_emits_log_event(
    client_with_key: TestClient,
    base_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Every InterviewRunnerError emits a structured pipeline.jsonl entry."""
    import json as _json

    log_calls: list[dict[str, object]] = []

    def _fake_log(event_type: str, **kwargs: object) -> None:
        log_calls.append({"event": event_type, **kwargs})

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.log_event", _fake_log)

    sid = _create_session_directly(base_root)
    _stub_run_turn_error(monkeypatch, "boom", kind="auth", status_code=401)
    client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "anything"},
    )

    err_events = [c for c in log_calls if c["event"] == "onboarding_interview_error"]
    assert len(err_events) == 1
    assert err_events[0]["session_id"] == sid
    assert err_events[0]["route"] == "turn"
    assert err_events[0]["error_kind"] == "auth"
    assert err_events[0]["status_code"] == 401
    # Just exercise the JSON shape — don't assert on serialization specifics
    _json.dumps(err_events[0])


def test_start_error_emits_log_event(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/start errors also log via the same channel — different route value."""
    _plant_credentials(base_root)
    log_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "findajob.web.routes.onboarding_interview.log_event",
        lambda evt, **kwargs: log_calls.append({"event": evt, **kwargs}),
    )
    _stub_run_turn_error(monkeypatch, "boom", kind="upstream", status_code=503)
    client_with_key.post("/onboarding/interview/start")

    err_events = [c for c in log_calls if c["event"] == "onboarding_interview_error"]
    assert len(err_events) == 1
    assert err_events[0]["route"] == "start"
    assert err_events[0]["error_kind"] == "upstream"
    assert err_events[0]["status_code"] == 503


# ── Tab-close-resume (#336 Task 8) ────────────────────────────────────────


def _age_session_last_turn(base_root: Path, session_id: str, sql_offset: str) -> None:
    """Override last_turn_at via raw SQL so we can simulate stale sessions."""
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        conn.execute(
            f"UPDATE onboarding_sessions SET last_turn_at = datetime('now', '{sql_offset}') WHERE id = ?",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()


def test_resume_index_no_affordance_when_no_session(client_with_key: TestClient) -> None:
    """Fresh stack with no in-progress session → no resume banner."""
    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="resume-banner"' not in body
    assert "/onboarding/interview/" not in body or "/onboarding/interview/start" in body


def test_resume_index_shows_affordance_when_active_session_exists(client_with_key: TestClient, base_root: Path) -> None:
    """An un-completed session with recent activity surfaces a resume link.
    Index also requires Step 1 keys to be present (post-2026-05-02 gating)."""
    from findajob.onboarding.session_store import append_turn

    sid = _plant_credentials(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        append_turn(conn, sid, "user", "kickoff")
        append_turn(conn, sid, "assistant", "first question?")
    finally:
        conn.close()

    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="resume-banner"' in body
    assert f"/onboarding/interview/{sid}" in body
    assert "Resume" in body or "resume" in body
    assert "minute" in body.lower() or "just now" in body.lower() or "hour" in body.lower()


def test_resume_link_lands_on_chat_with_full_history(client_with_key: TestClient, base_root: Path) -> None:
    """Click resume → chat page renders the full persisted history."""
    from findajob.onboarding.session_store import append_turn

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        append_turn(conn, sid, "user", "MARK_USER_RESUME")
        append_turn(conn, sid, "assistant", "MARK_ASSISTANT_RESUME")
    finally:
        conn.close()

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    assert "MARK_USER_RESUME" in body
    assert "MARK_ASSISTANT_RESUME" in body


def test_resume_index_excludes_completed_sessions(client_with_key: TestClient, base_root: Path) -> None:
    """A finalized session must NOT show a resume affordance — onboarding is done."""
    from findajob.onboarding.session_store import mark_complete

    sid = _create_session_directly(base_root)
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        mark_complete(conn, sid)
    finally:
        conn.close()

    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    assert 'id="resume-banner"' not in resp.text


def test_resume_index_excludes_stale_sessions(client_with_key: TestClient, base_root: Path) -> None:
    """A session whose last activity was > 24h ago should NOT surface."""
    sid = _create_session_directly(base_root)
    _age_session_last_turn(base_root, sid, "-25 hours")

    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    assert 'id="resume-banner"' not in resp.text


def test_resume_index_no_affordance_when_operator_key_unset(client_no_key: TestClient, base_root: Path) -> None:
    """When the operator hasn't opted in, surfacing a resume affordance
    would point at a router that isn't even registered. Suppress it."""
    sid = _create_session_directly(base_root)
    from findajob.onboarding.session_store import append_turn

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        append_turn(conn, sid, "user", "kickoff")
    finally:
        conn.close()

    resp = client_no_key.get("/onboarding/")
    assert resp.status_code == 200
    assert 'id="resume-banner"' not in resp.text
