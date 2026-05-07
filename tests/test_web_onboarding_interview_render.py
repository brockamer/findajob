"""Render-layer tests for #336 in-app interview templates (Task 5).

Distinct from `test_web_onboarding_interview_routes.py` (which exercises the
route logic + DB observable state). These tests assert on the rendered HTML
shape so the chat UI is correct independent of route behavior:
- /onboarding/ shows the interview affordance only when tester credentials
  have been collected at Step 1
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


def _make_client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


# After the OPENROUTER_OPERATOR_KEY revert (#401), the env-var distinction is
# gone — there is only one client shape. The fixtures stay as aliases so
# existing test bodies continue to read naturally; the difference between
# "with key" and "no key" is now whether _plant_credentials has been called.
@pytest.fixture
def client_with_key(base_root: Path) -> TestClient:
    return _make_client(base_root)


@pytest.fixture
def client_no_key(base_root: Path) -> TestClient:
    return _make_client(base_root)


# ── /onboarding/ landing-page affordance ─────────────────────────────────


def test_index_shows_interview_affordance_when_keys_collected(client_with_key: TestClient, base_root: Path) -> None:
    """Step 2 enables — and the Start interview button submits to /start —
    once Step 1 credentials are saved."""
    _plant_credentials(base_root)
    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    # Form posts to /start. No "disabled" attribute on the fieldset.
    assert "/onboarding/interview/start" in body
    assert 'disabled aria-disabled="true"' not in body


def test_index_disables_step_two_when_keys_not_collected(client_with_key: TestClient) -> None:
    """Without Step 1 keys, the Start interview button is disabled."""
    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    assert 'disabled aria-disabled="true"' in body
    assert "Save your API keys above before continuing." in body


def _plant_credentials(base_root: Path) -> str:
    """Helper: insert a credentials-only session row directly."""
    from findajob.onboarding.session_store import create_session, set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        sid = create_session(conn)
        set_credentials(
            conn,
            sid,
            openrouter_api_key="sk-or-v1-render-test",
            rapidapi_key="",
        )
    finally:
        conn.close()
    return sid


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
    # When NOT ready, the finalize form must not be present in the rendered HTML.
    assert f"/onboarding/interview/{sid}/finalize" not in body


# ── Task 6: finalize-block OOB placeholder (#401 PR B) ───────────────────


def test_finalize_block_placeholder_present_when_not_ready(client_with_key: TestClient, base_root: Path) -> None:
    """<section id="finalize-block"> must exist in the DOM even when finalize_ready=False.
    HTMX OOB swaps targeting #finalize-block require the element to be present at first
    page load or the swap silently fails — Finalize button never appears without a reload."""
    sid = _create_session_with_history(base_root, [{"role": "user", "content": "hi"}])
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    # Section must be present (empty placeholder for OOB target)
    assert 'id="finalize-block"' in body
    # No green-border class on the empty placeholder
    assert "border-green-300" not in body
    # No finalize form action
    assert f"/onboarding/interview/{sid}/finalize" not in body


def test_finalize_block_has_green_styling_and_button_when_ready(client_with_key: TestClient, base_root: Path) -> None:
    """When finalize_ready=True the section must carry the green-border styling
    and contain the Finalize submit button."""
    from findajob.onboarding.parser import ALLOWED_FILENAMES, parse_emission
    from findajob.onboarding.session_store import update_captured_blocks

    blob = "\n\n".join(f"<<<FILE: {name}>>>\nbody for {name}\n<<<END FILE: {name}>>>" for name in ALLOWED_FILENAMES)
    captured = parse_emission(blob).found

    sid = _create_session_with_history(base_root, [])
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        update_captured_blocks(conn, sid, captured)
    finally:
        conn.close()

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="finalize-block"' in body
    assert "border-green-300" in body
    assert "bg-green-50" in body
    assert f"/onboarding/interview/{sid}/finalize" in body


def test_interview_page_shows_finalize_block_when_all_blocks_captured(
    client_with_key: TestClient, base_root: Path
) -> None:
    """When all ALLOWED_FILENAMES are in captured_blocks, finalize unhides.
    The form has no OpenRouter input field — keys come from Step 1 creds."""
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
    # OR-key input field was removed — keys come from Step 1 credentials.
    assert 'name="openrouter_api_key"' not in body


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
    # /turn now requires the session's credentials to resolve a chat key
    # (no operator-env fallback after #401). Bind a tester key to this row.
    from findajob.onboarding.session_store import set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        set_credentials(conn, sid, openrouter_api_key="sk-or-v1-render-test", rapidapi_key="")
    finally:
        conn.close()

    def _fake(api_key, history, user_message):
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


# ── Start Interview button loading state (#401 PR B Task 2) ──────────────


def test_start_interview_button_has_alpine_loading_state(client_with_key: TestClient, base_root: Path) -> None:
    """When keys are collected the Start Interview button carries Alpine.js
    reactivity to disable itself and swap to a spinner label on submit."""
    _plant_credentials(base_root)
    resp = client_with_key.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    assert 'x-data="{ starting: false }"' in body
    assert ':disabled="starting"' in body
    assert "animate-spin" in body


# ── Markdown rendering + FILE-block badging (#401 PR B Task 3) ───────────


def _bind_credentials(base_root: Path, session_id: str) -> None:
    from findajob.onboarding.session_store import set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        set_credentials(conn, session_id, openrouter_api_key="sk-or-v1-render-test", rapidapi_key="")
    finally:
        conn.close()


def test_turn_partial_file_block_shows_badge_not_raw_delimiter(
    client_with_key: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the assistant emits a FILE block, the HTMX partial must render
    a captured-file badge instead of the raw <<<FILE:>>> markers (#401 PR B Task 3)."""
    sid = _create_session_with_history(base_root, [])
    _bind_credentials(base_root, sid)

    emission_turn = (
        "Your profile has been captured:\n\n"
        "<<<FILE: profile.md>>>\nname: Test User\nrole: tester\n<<<END FILE: profile.md>>>\n\n"
        "Let's continue with the next section."
    )

    monkeypatch.setattr(
        "findajob.web.routes.onboarding_interview.run_turn",
        lambda *a, **kw: (emission_turn, {}),
    )

    resp = client_with_key.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "ready"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Badge must be present
    assert "captured-file" in body
    # Raw delimiter must NOT appear
    assert "<<<FILE:" not in body
    assert "<<<END FILE:" not in body
    # Block body (multi-KB in production) must not bleed through
    assert "name: Test User" not in body


def test_resume_page_file_block_shows_badge_not_raw_delimiter(client_with_key: TestClient, base_root: Path) -> None:
    """On full-page resume load (GET /onboarding/interview/{sid}), FILE blocks
    in persisted history must appear as badges, not raw markers (#401 PR B Task 3)."""
    emission_content = (
        "Here is your profile:\n\n"
        "<<<FILE: profile.md>>>\nname: Stored User\n<<<END FILE: profile.md>>>\n\n"
        "Continuing the interview."
    )
    sid = _create_session_with_history(
        base_root,
        [
            {"role": "user", "content": "Begin the interview."},
            {"role": "assistant", "content": emission_content},
        ],
    )

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    # Badge must appear for the stored FILE block
    assert "captured-file" in body
    # Raw delimiter must not leak into rendered HTML
    assert "<<<FILE:" not in body
    assert "<<<END FILE:" not in body
    # Block body must not appear
    assert "name: Stored User" not in body


# The onboarding-cost nav chip OOB swap was retired in #87 and the credits
# chip itself was retired in #472 (v0.20.0). The nav now shows current-month
# spend from cost_log directly — no per-turn OOB swap needed.
