"""Render-layer tests for #336 in-app interview templates (Task 5).

Distinct from `test_web_onboarding_interview_routes.py` (which exercises the
route logic + DB observable state). These tests assert on the rendered HTML
shape so the chat UI is correct independent of route behavior:
- /onboarding/ shows the interview affordance only when operator_key is set
- interview.html: messages list, HTMX wiring, finalize visibility
- _turn.html: single bubble, role-styled
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

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


def _make_client(base_root: Path, *, operator_key: str | None) -> TestClient:
    import os

    if operator_key is None:
        os.environ.pop("OPENROUTER_OPERATOR_KEY", None)
    else:
        os.environ["OPENROUTER_OPERATOR_KEY"] = operator_key
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client_with_key(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENROUTER_OPERATOR_KEY", _OPERATOR_KEY)
    return _make_client(base_root, operator_key=_OPERATOR_KEY)


@pytest.fixture
def client_no_key(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("OPENROUTER_OPERATOR_KEY", raising=False)
    return _make_client(base_root, operator_key=None)


# ── /onboarding/ landing-page affordance ─────────────────────────────────


def test_index_shows_interview_affordance_when_operator_key_set(client_with_key: TestClient) -> None:
    """Per Task 5: when operator_mode_interview_enabled, render two affordances."""
    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text

    # The "Run interview here" affordance is a form/link that posts to /start
    assert "/onboarding/interview/start" in body
    assert "run interview here" in body.lower() or "interview here" in body.lower()

    # The paste-back affordance must still be present (it's the fallback)
    assert 'name="emission"' in body
    assert "i already ran" in body.lower() or "already ran" in body.lower() or "paste" in body.lower()


def test_index_hides_interview_affordance_when_operator_key_unset(client_no_key: TestClient) -> None:
    """When operator key isn't set, the chat affordance must NOT render
    (would 404 on click — broken affordance, acceptance #6)."""
    resp = client_no_key.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text

    # No link/form pointing at the in-app interview start route
    assert "/onboarding/interview/start" not in body

    # Paste-back is still the operative path
    assert 'name="emission"' in body


# ── /onboarding/interview/{sid} (resume page renders interview.html) ─────


def _create_session_with_history(base_root: Path, history: list[dict[str, str]]) -> str:
    """Insert a session row with pre-seeded history."""
    import json

    from findajob.onboarding.session_store import (
        append_turn,
        create_session,
    )

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
        for turn in history:
            append_turn(conn, sid, turn["role"], turn["content"])
        # Sanity: history is what we expect
        row = conn.execute("SELECT history_json FROM onboarding_sessions WHERE id = ?", (sid,)).fetchone()
        assert json.loads(row[0]) == history
    finally:
        conn.close()
    return sid


def test_interview_page_renders_message_list_div(client_with_key: TestClient, base_root: Path) -> None:
    """interview.html must include `<div id="messages">` so HTMX hx-target=#messages
    has somewhere to append (Task 5 acceptance)."""
    sid = _create_session_with_history(base_root, [])
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    assert 'id="messages"' in resp.text


def test_interview_page_renders_persisted_history_with_role_styling(
    client_with_key: TestClient, base_root: Path
) -> None:
    """Each persisted turn should appear with role attribution."""
    sid = _create_session_with_history(
        base_root,
        [
            {"role": "user", "content": "MARK_USER_MSG"},
            {"role": "assistant", "content": "MARK_ASSISTANT_MSG"},
        ],
    )
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    assert "MARK_USER_MSG" in body
    assert "MARK_ASSISTANT_MSG" in body
    # Role-styled: each bubble carries data-role so CSS / tests can target
    assert 'data-role="user"' in body
    assert 'data-role="assistant"' in body


def test_interview_page_includes_htmx_post_form_targeting_messages(
    client_with_key: TestClient, base_root: Path
) -> None:
    """The user-input form must HTMX-post to /turn with hx-target=#messages
    and hx-swap=beforeend (Task 5 acceptance)."""
    sid = _create_session_with_history(base_root, [])
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    assert 'hx-post="/onboarding/interview/turn"' in body
    assert 'hx-target="#messages"' in body
    assert 'hx-swap="beforeend"' in body


def test_interview_page_hides_finalize_block_when_not_ready(client_with_key: TestClient, base_root: Path) -> None:
    """Finalize block must be hidden until captured_count == required_count."""
    sid = _create_session_with_history(base_root, [{"role": "user", "content": "hi"}])
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    # The finalize form posts to /{sid}/finalize and contains the API-key input.
    # When NOT ready, that form must not be present in the rendered HTML.
    assert f"/onboarding/interview/{sid}/finalize" not in body
    assert 'name="openrouter_api_key"' not in body


def test_interview_page_shows_finalize_block_when_all_blocks_captured(
    client_with_key: TestClient, base_root: Path
) -> None:
    """When all ALLOWED_FILENAMES are in captured_blocks, finalize unhides."""
    from findajob.onboarding.parser import ALLOWED_FILENAMES, parse_emission

    blob = "\n\n".join(f"<<<FILE: {name}>>>\nbody for {name}\n<<<END FILE: {name}>>>" for name in ALLOWED_FILENAMES)
    captured = parse_emission(blob).found

    sid = _create_session_with_history(base_root, [])
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        from findajob.onboarding.session_store import update_captured_blocks

        update_captured_blocks(conn, sid, captured)
    finally:
        conn.close()

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    assert f"/onboarding/interview/{sid}/finalize" in body
    assert 'name="openrouter_api_key"' in body


def test_interview_page_shows_progress_count(client_with_key: TestClient, base_root: Path) -> None:
    """A small badge / progress hint surfaces captured_count / required_count."""
    sid = _create_session_with_history(base_root, [])
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    # 0 of 10 (or whatever the current ALLOWED_FILENAMES length is)
    from findajob.onboarding.parser import ALLOWED_FILENAMES

    assert f"of {len(ALLOWED_FILENAMES)}" in resp.text or f"/{len(ALLOWED_FILENAMES)}" in resp.text


# ── /turn partial (returned response shape) ──────────────────────────────


def test_turn_response_renders_user_and_assistant_bubbles(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The /turn response (a partial appended into #messages) must contain
    BOTH the user's just-sent message AND the assistant's reply, each with
    its own role-styled bubble. Otherwise the user's message disappears
    from view after the HTMX swap."""
    sid = _create_session_with_history(base_root, [])

    def _fake(operator_key, system_prompt, history, user_message):
        return "ASSISTANT_REPLY_MARKER", {}

    monkeypatch.setattr("findajob.web.routes.onboarding_interview.run_turn", _fake)

    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "USER_MSG_MARKER"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "USER_MSG_MARKER" in body
    assert "ASSISTANT_REPLY_MARKER" in body
    assert 'data-role="user"' in body
    assert 'data-role="assistant"' in body
