"""End-to-end integration test for the in-app onboarding interview (#336 Task 9).

Exercises the full code path with TestClient + a mocked
``urllib.request.urlopen`` (the only external dependency in the runner)
+ a stubbed openrouter_smoke check (so finalize doesn't hit the real
network). The integration covers:

    POST /onboarding/interview/start              — synthetic kickoff turn
    POST /onboarding/interview/turn (×N)          — multi-turn accumulation
    POST /onboarding/interview/{sid}/finalize     — invokes inject() pipeline

Asserts: the user's /finalize call writes every required file under
``base_root``, the onboarding sentinel exists, and the session row is
marked complete with ``captured_blocks`` covering every
``ALLOWED_FILENAMES`` entry.

Distinct from ``test_web_onboarding_interview_routes.py`` (which mocks
``run_turn`` directly). This test mocks one level deeper — the underlying
HTTP call — so it also pins the runner's payload-shape contract end to end.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

# Schema is built from the production migration runner so the fixture
# matches the real shape exactly. Pre-M5 a hand-written CREATE TABLE
# block lived here and drifted whenever a column was added.

_USER_KEY = "sk-or-v1-user-test"

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "onboarding"


class _FakeResp:
    """urlopen() context-manager-compatible fake — same shape as the
    runner's existing test helper. Body is bytes; the runner decodes
    UTF-8 and parses JSON."""

    def __init__(self, body: bytes) -> None:
        self._buf = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._buf.getvalue()


def _ok_resp(text: str) -> _FakeResp:
    body = {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200},
    }
    return _FakeResp(json.dumps(body).encode("utf-8"))


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    """Set up a tmp BASE that mirrors the layout inject() expects."""
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context" / "voice_samples").mkdir(parents=True)
    (tmp_path / "config" / "roles").mkdir(parents=True)
    (tmp_path / ".backups").mkdir()
    (tmp_path / "logs").mkdir()
    repo_role = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"
    shutil.copy(repo_role, tmp_path / "config" / "roles" / "onboarding_interviewer.md")

    from findajob.db.migrate import apply_pending

    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_openrouter_smoke_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The post-paste OpenRouter smoke check would fire a real network
    call inside inject(). Stub it everywhere so tests stay hermetic."""
    import findajob.onboarding.injector as inj_mod

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", lambda _k: (True, None))


@pytest.fixture
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _split_emission_into_two_chunks(blob: str) -> tuple[str, str]:
    """Split the canonical emission fixture roughly in half between
    `<<<END FILE>>>` boundaries so each chunk is a valid suffix of
    self-contained blocks. Returns (chunk1, chunk2)."""
    end_marker = "<<<END FILE: "
    parts = blob.split(end_marker)
    # parts[0] is everything before the first END FILE; parts[i] for i>=1
    # starts at "filename>>>". Reassemble after halving.
    midpoint = len(parts) // 2
    chunk1 = end_marker.join(parts[:midpoint]) + end_marker + parts[midpoint].split(">>>", 1)[0] + ">>>"
    rest_of_first = parts[midpoint].split(">>>", 1)[1]
    chunk2 = rest_of_first + end_marker + end_marker.join(parts[midpoint + 1 :])
    return chunk1.lstrip("\n"), chunk2.lstrip("\n")


def test_full_interview_flow_writes_all_files_and_sentinel(
    client: TestClient,
    base_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive a real flow: /start → /turn (kickoff) → /turn → /turn → /finalize.

    Mock urlopen so each call returns a canned LLM reply.

    #755 deferred /start's synchronous LLM call out of the route — /start
    is now a fast session-resolve + 303 with no LLM hit. The kickoff turn
    is driven from the chat page on load (production: /turn-stream
    auto-fire; this integration test simulates the same outcome via /turn
    since the inject path under test is downstream of the streaming/sync
    transport choice — both routes append the same shape of turns to the
    session row)."""
    from findajob.onboarding.parser import ALLOWED_FILENAMES

    blob = (_FIXTURE_DIR / "alice-doe-clean-emission.txt").read_text(encoding="utf-8")
    chunk1, chunk2 = _split_emission_into_two_chunks(blob)
    # Sanity: chunks together cover every required block (uses the same
    # emission a paste-back run would inject successfully)
    from findajob.onboarding.parser import parse_emission

    combined_parse = parse_emission(chunk1 + "\n\n" + chunk2)
    assert not combined_parse.missing, f"fixture split lost blocks: {combined_parse.missing}"

    canned_responses = [
        # Response to the kickoff /turn — orientation prose (post-#755 this
        # turn is auto-fired by the chat page; pre-#755 it came from /start).
        "Welcome to the findajob onboarding interview. I'll ask you a few "
        "questions to set up your pipeline. First — what role are you targeting?",
        # Response to /turn #1 — first half of the emission blocks.
        "Got it. Here are the first set of files to inject:\n\n" + chunk1,
        # Response to /turn #2 — second half.
        "Great, thanks. And here is the rest:\n\n" + chunk2,
    ]
    response_iter = iter(canned_responses)

    def _fake_urlopen(req, timeout=None):
        return _ok_resp(next(response_iter))

    monkeypatch.setattr(
        "findajob.llm.openrouter.urllib.request.urlopen",
        _fake_urlopen,
    )

    # ── Step 1: plant credentials (mandatory before /start since 2026-05-02) ──
    from findajob.onboarding.session_store import create_session, set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        creds_sid = create_session(conn)
        set_credentials(
            conn,
            creds_sid,
            openrouter_api_key=_USER_KEY,
            rapidapi_key="",
        )
    finally:
        conn.close()

    # ── /start ────────────────────────────────────────────────────────
    resp = client.post("/onboarding/interview/start")
    assert resp.status_code == 303, resp.text
    sid = resp.headers["location"].rsplit("/", 1)[-1]
    # /start resolves the credentials-only row, so the chat session id
    # should equal the credentials-only id (same row, history attached
    # below by the kickoff /turn).
    assert sid == creds_sid

    # ── Kickoff /turn ─────────────────────────────────────────────────
    # Post-#755 the kickoff turn is driven from the chat page on load
    # (production: /turn-stream auto-fire). Simulating via /turn here —
    # same session-store writes, same urlopen mock catches the LLM call.
    resp_kickoff = client.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "Begin the interview."},
    )
    assert resp_kickoff.status_code == 200, resp_kickoff.text

    # ── /turn × 2 ─────────────────────────────────────────────────────
    resp1 = client.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "Clinical social worker in community mental health."},
    )
    assert resp1.status_code == 200, resp1.text

    resp2 = client.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "I'm ready for the rest."},
    )
    assert resp2.status_code == 200, resp2.text

    # All required blocks should now be in captured_blocks
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            "SELECT captured_blocks_json FROM onboarding_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    captured = json.loads(row[0])
    assert set(captured.keys()) >= set(ALLOWED_FILENAMES), (
        f"missing blocks before finalize: {set(ALLOWED_FILENAMES) - set(captured.keys())}"
    )

    # ── /finalize ────────────────────────────────────────────────────
    # No form data — /finalize reads keys from the session's credentials,
    # which we planted before /start.  Per #984, finalize now redirects to the
    # feed-check step (validate emitted feed_urls.txt), which leads to the
    # timezone-confirmation step (#989), then spend-ceiling (#671), whose
    # /finish then makes the feed-config vs gmail-config decision. The sentinel
    # is written by the connections gate.
    resp_fin = client.post(f"/onboarding/interview/{sid}/finalize")
    assert resp_fin.status_code == 303, resp_fin.text
    assert resp_fin.headers["location"] == f"/onboarding/feed-check/{sid}/"

    # ── Filesystem assertions ────────────────────────────────────────
    # Every destination file the inject() path writes must be present.
    # (The sentinel itself is intentionally absent — gmail-config gate writes
    # it after the user completes or skips that step.)
    expected_files = [
        base_root / "candidate_context" / "profile.md",
        base_root / "candidate_context" / "master_resume.md",
        base_root / "candidate_context" / "display_name.txt",
        base_root / "config" / "target_companies.md",
        base_root / "config" / "business_sector_employers_reference.md",
        base_root / "config" / "jsearch_queries.txt",
        base_root / "config" / "prefilter_rules.yaml",
        base_root / "config" / "in_domain_patterns.yaml",
        base_root / "config" / "reject_reasons.yaml",
        base_root / "data" / "timezone",
        base_root / "data" / ".env",  # ntfy_topic merged here
    ]
    for f in expected_files:
        assert f.is_file(), f"expected file missing: {f}"
    assert not (base_root / "data" / ".onboarding-complete").exists(), (
        "sentinel must be deferred to the connections gate (#571), downstream of gmail-config (#407)"
    )

    # Gmail-config /skip hands off to the connections gate without writing
    # the sentinel — that responsibility moved to the connections gate (#571).
    resp_gmail_skip = client.post(f"/onboarding/gmail-config/{sid}/skip")
    assert resp_gmail_skip.status_code == 303, resp_gmail_skip.text
    assert resp_gmail_skip.headers["location"] == f"/onboarding/connections/{sid}/"
    assert not (base_root / "data" / ".onboarding-complete").exists()

    # Connections gate /skip is the terminal step — writes the sentinel and
    # redirects to the dashboard.
    resp_conn_skip = client.post(f"/onboarding/connections/{sid}/skip")
    assert resp_conn_skip.status_code == 303, resp_conn_skip.text
    assert resp_conn_skip.headers["location"] == "/board/dashboard"
    assert (base_root / "data" / ".onboarding-complete").is_file()

    # Session is marked complete
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute("SELECT completed_at FROM onboarding_sessions WHERE id = ?", (sid,)).fetchone()
        # Three turns (/start + /turn + /turn) → three cost_log rows (#463).
        cost_rows = conn.execute("SELECT COUNT(*) FROM cost_log WHERE operation = 'onboarding_interviewer'").fetchone()[
            0
        ]
    finally:
        conn.close()
    assert row[0] is not None, "session was not marked complete after finalize"
    assert cost_rows == 3, f"expected 3 onboarding_interviewer cost_log rows, got {cost_rows}"


def test_full_interview_flow_skips_finalize_when_blocks_missing(
    client: TestClient,
    base_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LLM never emits a block, /finalize 400s with a missing-blocks
    error and the sentinel is never written. Single round-trip."""
    canned_responses = [
        "Welcome — what role are you targeting?",
        "Got it. (No blocks emitted yet — interview in progress.)",
    ]
    response_iter = iter(canned_responses)
    monkeypatch.setattr(
        "findajob.llm.openrouter.urllib.request.urlopen",
        lambda req, timeout=None: _ok_resp(next(response_iter)),
    )

    # Plant Step 1 credentials so /start can promote them.
    from findajob.onboarding.session_store import create_session, set_credentials

    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        creds_sid = create_session(conn)
        set_credentials(
            conn,
            creds_sid,
            openrouter_api_key=_USER_KEY,
            rapidapi_key="",
        )
    finally:
        conn.close()

    resp_start = client.post("/onboarding/interview/start")
    sid = resp_start.headers["location"].rsplit("/", 1)[-1]
    client.post(
        "/onboarding/interview/turn",
        data={"session_id": sid, "message": "social worker"},
    )

    resp_fin = client.post(f"/onboarding/interview/{sid}/finalize")
    assert resp_fin.status_code == 400
    assert "missing" in resp_fin.text.lower() or "not yet complete" in resp_fin.text.lower()
    assert not (base_root / "data" / ".onboarding-complete").exists()
