"""Render-layer tests for #336 in-app interview templates (Task 5).

Distinct from `test_web_onboarding_interview_routes.py` (which exercises the
route logic + DB observable state). These tests assert on the rendered HTML
shape so the chat UI is correct independent of route behavior:
- /onboarding/ shows the interview affordance only when user credentials
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


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config" / "roles").mkdir(parents=True)
    repo_role = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"
    shutil.copy(repo_role, tmp_path / "config" / "roles" / "onboarding_interviewer.md")

    # Build the pipeline DB via the production migration runner so the
    # fixture's schema matches the real shape exactly. Pre-M5 a
    # hand-written CREATE TABLE block lived here and drifted whenever a
    # column was added.
    from findajob.db.migrate import apply_pending

    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
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


def test_interview_page_includes_streaming_form_pointing_at_turn_stream(
    client_with_key: TestClient, base_root: Path
) -> None:
    """The user-input form must drive the SSE streaming endpoint (#740).

    The form opts into the JS-driven streaming flow via
    `data-stream-endpoint="/onboarding/interview/turn-stream"`. The
    onboarding-stream.js module attaches a submit handler that POSTs there
    and consumes the SSE response progressively.
    """
    sid = _create_session_with_history(base_root, [])
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    assert 'data-stream-endpoint="/onboarding/interview/turn-stream"' in body
    # The streaming-progress slot the JS targets for badge chips.
    assert 'id="stream-progress"' in body
    # The JS module must be loaded so the form's submit handler attaches.
    assert "onboarding-stream.js" in body
    # No HTMX form attrs on the streaming form — they would race with the
    # JS handler and re-introduce the old non-streaming UX path.
    assert 'hx-post="/onboarding/interview/turn"' not in body
    # Negative containment: the streaming endpoint URL must NOT appear as
    # the non-streaming /turn (catches a lazy substring match).
    assert "/onboarding/interview/turn-stream" in body
    # base.html sets hx-boost="true" on <body>, which boosts all descendant
    # forms by default. Without an explicit opt-out, HTMX intercepts the
    # submit BEFORE onboarding-stream.js's handler runs and fires an XHR GET
    # to the form's default action — the streaming endpoint is never hit and
    # the chat page silently reload-renders. Caught in browser verification
    # against findajob-clean after #740 merged; one-line regression guard.
    assert 'hx-boost="false"' in body
    # Pair with structural containment: the opt-out must be on the streaming
    # form specifically. Look at the rendered slice containing the form tag.
    streaming_form_idx = body.find('data-stream-endpoint="/onboarding/interview/turn-stream"')
    # The opt-out attribute must live on the same <form ...> opening tag —
    # search backward for the form open and forward for the tag close, and
    # assert the hx-boost="false" landed inside that range.
    form_open = body.rfind("<form", 0, streaming_form_idx)
    form_close = body.find(">", streaming_form_idx)
    streaming_form_tag = body[form_open : form_close + 1]
    assert 'hx-boost="false"' in streaming_form_tag, (
        "hx-boost='false' must be on the streaming form's opening tag, not a sibling element. "
        f"Tag was: {streaming_form_tag!r}"
    )


def test_interview_page_streaming_form_contains_submit_button_for_harness(
    client_with_key: TestClient, base_root: Path
) -> None:
    """Pin the exact selector path scripts/walkthrough_harness.py clicks to
    submit a user turn:
    `form[data-stream-endpoint="/onboarding/interview/turn-stream"] button[type="submit"]`.

    The companion test above pins the form-level attributes (data-stream-endpoint,
    hx-boost='false', module load). This one pins the *button-inside-form*
    relationship — a refactor that, for example, removes the <form> wrapper
    and drives submission from an Alpine `@click` on a bare <button> would
    silently break the harness's selector and only fail during an audit run.
    Caught here at unit-test time per #750.
    """
    sid = _create_session_with_history(base_root, [])
    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text

    # Slice out the streaming form's full HTML so the submit-button assertion
    # can't be satisfied by any other form that might live on the same page.
    endpoint_idx = body.find('data-stream-endpoint="/onboarding/interview/turn-stream"')
    assert endpoint_idx != -1, "streaming form (data-stream-endpoint) not found in page"
    form_open = body.rfind("<form", 0, endpoint_idx)
    form_close = body.find("</form>", endpoint_idx)
    assert form_open != -1 and form_close != -1, "could not bracket streaming form HTML"
    streaming_form_html = body[form_open : form_close + len("</form>")]

    # Positive: the harness's click target must live inside the streaming form.
    assert 'type="submit"' in streaming_form_html, (
        'Streaming form must contain a `<button type="submit">` so '
        "`scripts/walkthrough_harness.py` can click it to drive a chat turn. "
        f"Form HTML was: {streaming_form_html!r}"
    )
    # Negative pair: a future refactor that re-introduces hx-post on the
    # streaming form would race with the JS submit handler (same shape as
    # the original #740 regression that necessitated `hx-boost='false'`).
    # Defends in depth alongside the global-absence check in the companion test.
    assert "hx-post=" not in streaming_form_html, (
        "Streaming form must not carry hx-post attrs; submission is JS-driven "
        f"via the SSE endpoint. Form HTML was: {streaming_form_html!r}"
    )


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


def test_interview_page_finalize_membership_not_cardinality(client_with_key: TestClient, base_root: Path) -> None:
    """#626 regression: finalize_ready must check membership of every ALLOWED_FILENAMES
    name, not just `len(captured) >= len(ALLOWED_FILENAMES)`. A session with 7 required +
    3 optional captures (10 entries total) hit the green banner pre-fix because the
    predicate counted cardinality; POST /finalize then 400'd with the 3 missing required
    yaml filenames. The fix routes the chat-render predicate through the same membership
    check the /finalize handler already used.
    """
    from findajob.onboarding.parser import ALLOWED_FILENAMES, OPTIONAL_FILENAMES
    from findajob.onboarding.session_store import update_captured_blocks

    # 7 required + 3 optional = 10 total entries — same cardinality as len(ALLOWED_FILENAMES)
    required_subset = list(ALLOWED_FILENAMES[:7])
    optional_subset = list(OPTIONAL_FILENAMES[:3])
    captured = {name: f"body for {name}" for name in (*required_subset, *optional_subset)}
    assert len(captured) == len(ALLOWED_FILENAMES), "fixture invariant: same cardinality"
    missing_required = set(ALLOWED_FILENAMES) - set(required_subset)
    assert len(missing_required) == 3, "fixture invariant: 3 required still missing"

    sid = _create_session_with_history(base_root, [])
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        update_captured_blocks(conn, sid, captured)
    finally:
        conn.close()

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text
    # Green banner must be suppressed — the finalize form and the green styling
    # are the visible signal that misled the user in the original report.
    assert f"/onboarding/interview/{sid}/finalize" not in body
    assert "border-green-300" not in body
    # Progress badge must reflect required-only matches, not total captures —
    # otherwise testers see "10 of 10" with optional entries padding the count.
    assert "Captured 7 of 10 required blocks" in body


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
    # (no operator-env fallback after #401). Bind a user key to this row.
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


# ── #755 kickoff-pending markers on the chat page ─────────────────────────


def test_interview_page_carries_kickoff_pending_markers_when_history_empty(
    client_with_key: TestClient, base_root: Path
) -> None:
    """#755: the chat page exposes ``data-kickoff-pending="true"`` and
    ``data-kickoff-message="…"`` on the chat container when history is
    empty. The JS auto-fire handler reads these to know it should POST to
    /turn-stream with the kickoff message instead of waiting for the user's
    first submit.

    Server-authoritative source of truth: the kickoff message lives as a
    Python constant in the route module and is rendered into the page —
    no JS-side copy that could drift.
    """
    sid = _create_session_with_history(base_root, [])

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text

    assert 'data-kickoff-pending="true"' in body, "kickoff-pending marker missing on empty-history chat page"
    assert 'data-kickoff-message="Begin the interview."' in body, "kickoff-message attribute missing or wrong text"


def test_interview_page_omits_kickoff_pending_marker_when_history_non_empty(
    client_with_key: TestClient, base_root: Path
) -> None:
    """#755 negative control: once the kickoff has fired (history has any
    turn), the JS must NOT re-fire on subsequent page loads. The server
    omits ``data-kickoff-pending`` so the JS auto-fire branch is skipped.

    The ``data-kickoff-message`` attribute may still be present (the
    constant doesn't change) but the JS only acts on it when paired with
    the ``data-kickoff-pending`` marker.
    """
    sid = _create_session_with_history(
        base_root,
        [
            {"role": "user", "content": "Begin the interview."},
            {"role": "assistant", "content": "Welcome — what role are you targeting?"},
        ],
    )

    resp = client_with_key.get(f"/onboarding/interview/{sid}")
    assert resp.status_code == 200
    body = resp.text

    assert "data-kickoff-pending" not in body, "kickoff-pending marker must not appear when history is non-empty"
