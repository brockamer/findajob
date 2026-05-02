"""Tests for POST /onboarding/keys (#339 Step 1).

The route collects three API keys, runs format + smoke validation, and
persists into the credentials-only session row in onboarding_sessions.
The UPDATE-not-INSERT semantic on retry prevents orphan rows from
shadowing successful submissions in find_credentials_only().
"""

from __future__ import annotations

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

_VALID_OR = "sk-or-v1-tester-fake-test-1234"
_VALID_RAPID = "fakeRapidApiTesterKey1234567890abcdef"
_VALID_GOOGLE = "AIzaFakeGoogleTesterKey1234567890ab"


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.close()
    return tmp_path


@pytest.fixture
def client(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Stub the live OpenRouter smoke check — tests must not make real network calls.
    import findajob.web.routes.onboarding as ob_routes

    monkeypatch.setattr(
        ob_routes,
        "verify_openrouter_key",
        lambda key: (True, None) if "tester" in key or "valid" in key else (False, "key invalid"),
    )
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _row_count(base_root: Path) -> int:
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        return conn.execute("SELECT COUNT(*) FROM onboarding_sessions").fetchone()[0]
    finally:
        conn.close()


def _stored_credentials(base_root: Path) -> tuple[str | None, str | None, str | None] | None:
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            """SELECT tester_openrouter_key, tester_rapidapi_key, tester_google_key
               FROM onboarding_sessions ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        return row if row else None
    finally:
        conn.close()


def test_post_all_three_valid_creates_one_row(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": _VALID_OR,
            "rapidapi_key": _VALID_RAPID,
            "google_api_key": _VALID_GOOGLE,
        },
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/"
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (_VALID_OR, _VALID_RAPID, _VALID_GOOGLE)


def test_post_only_openrouter_stores_optional_fields_null(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": _VALID_OR},
    )
    assert r.status_code == 303
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (_VALID_OR, None, None)


def test_post_malformed_openrouter_does_not_write_db(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": "not-a-valid-key"},
    )
    assert r.status_code == 400
    # Form preserves the optional inputs (here none, so just the error renders).
    assert "Couldn't save your keys" in r.text or "openrouter" in r.text.lower()
    assert _row_count(base_root) == 0


def test_post_smoke_failure_does_not_write_db(client: TestClient, base_root: Path) -> None:
    # The fixture's smoke stub rejects keys without "tester" or "valid" in them;
    # this key has the right prefix to pass format validation but fails live verify.
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": "sk-or-v1-rejected-by-smoke"},
    )
    assert r.status_code == 400
    assert "rejected" in r.text.lower() or "verify" in r.text.lower()
    assert _row_count(base_root) == 0


def test_post_twice_with_different_keys_keeps_one_row_with_second_values(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": _VALID_OR, "rapidapi_key": _VALID_RAPID},
    )
    assert r.status_code == 303
    assert _row_count(base_root) == 1

    second_or = "sk-or-v1-tester-different-key-xyz"
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": second_or, "google_api_key": _VALID_GOOGLE},
    )
    assert r.status_code == 303
    assert _row_count(base_root) == 1
    # Second submission's values win; UPDATE semantic preserved.
    creds = _stored_credentials(base_root)
    assert creds == (second_or, None, _VALID_GOOGLE)


def test_post_fail_fail_success_results_in_one_row(client: TestClient, base_root: Path) -> None:
    # Two failed attempts: one bad format, one bad smoke.
    r1 = client.post("/onboarding/keys", data={"openrouter_api_key": "garbage"})
    assert r1.status_code == 400
    r2 = client.post("/onboarding/keys", data={"openrouter_api_key": "sk-or-v1-rejected"})
    assert r2.status_code == 400
    # No orphan rows from either failure.
    assert _row_count(base_root) == 0
    # Now the successful third attempt.
    r3 = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": _VALID_OR},
    )
    assert r3.status_code == 303
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (_VALID_OR, None, None)


def test_get_index_after_collection_renders_step2_enabled(client: TestClient, base_root: Path) -> None:
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    r = client.get("/onboarding/")
    assert r.status_code == 200
    # Keys-collected state surfaces a "Change keys" affordance.
    assert "Change keys" in r.text
    # Last 4 of OpenRouter rendered (key ends in "1234").
    assert "1234" in r.text
    # Step 2 affordance enabled — fieldset has no "disabled" attribute on the
    # Start interview button.
    assert "Save your API keys above before continuing" not in r.text


def test_get_index_before_collection_renders_step2_disabled(client: TestClient, base_root: Path) -> None:
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "Save your API keys above before continuing" in r.text


def test_post_reset_clears_credentials(client: TestClient, base_root: Path) -> None:
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    assert _stored_credentials(base_root) == (_VALID_OR, None, None)
    r = client.post("/onboarding/keys", data={"reset": "1"})
    assert r.status_code == 303
    # Row stays; just credentials columns are cleared (chat history would also
    # remain if any existed — Task 4's "Change keys" semantic per plan).
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (None, None, None)


def test_post_invalid_rapidapi_does_not_write_db(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": _VALID_OR,
            "rapidapi_key": "key with spaces in it",  # whitespace forbidden
        },
    )
    assert r.status_code == 400
    assert _row_count(base_root) == 0


def test_post_invalid_google_does_not_write_db(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": _VALID_OR,
            "google_api_key": "wrong-prefix-key",  # missing AIza
        },
    )
    assert r.status_code == 400
    assert _row_count(base_root) == 0


def test_post_preserves_optional_inputs_on_failure(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": "garbage",
            "rapidapi_key": _VALID_RAPID,
            "google_api_key": _VALID_GOOGLE,
        },
    )
    assert r.status_code == 400
    # OpenRouter is intentionally NOT preserved; RapidAPI and Google are.
    assert _VALID_RAPID in r.text
    assert _VALID_GOOGLE in r.text


# test_inject_uses_credentials_from_step1_when_present — deleted 2026-05-02
# along with the paste-back path (/onboarding/inject). The equivalent
# coverage for the in-app finalize path is in
# tests/test_web_onboarding_interview_routes.py::test_finalize_calls_inject_and_marks_complete.


def test_already_onboarded_hint_renders_when_sentinel_present_no_keys(client: TestClient, base_root: Path) -> None:
    """Advisor follow-up to #339: an already-onboarded stack (sentinel
    present) where Step 1 hasn't been used renders a soft hint rather
    than asking the tester for keys they've never seen this UI ask for."""
    sentinel = base_root / "data" / ".onboarding-complete"
    sentinel.write_text("2026-04-29T00:00:00Z\n")
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "You've already onboarded" in r.text


def test_already_onboarded_hint_suppressed_in_rerun_mode(client: TestClient, base_root: Path) -> None:
    """Hint is for accidental visits. In ?mode=rerun the user is here
    on purpose — show the rerun banner, not the soft hint."""
    sentinel = base_root / "data" / ".onboarding-complete"
    sentinel.write_text("2026-04-29T00:00:00Z\n")
    r = client.get("/onboarding/?mode=rerun")
    assert r.status_code == 200
    assert "You've already onboarded" not in r.text
    assert "Re-running onboarding" in r.text


def test_already_onboarded_hint_suppressed_when_keys_collected(client: TestClient, base_root: Path) -> None:
    """If the tester has already used Step 1, they're past the
    accidentally-confused state — the keys-collected layout takes
    over and the hint is unnecessary."""
    sentinel = base_root / "data" / ".onboarding-complete"
    sentinel.write_text("2026-04-29T00:00:00Z\n")
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "You've already onboarded" not in r.text
    assert "Change keys" in r.text


def test_keys_collected_hides_step1_input(client: TestClient, base_root: Path) -> None:
    """When Step 1 credentials are saved, the index page renders a masked
    summary instead of the input form, with a Change keys link.

    Replaced 2026-05-02. The earlier test exercised paste-back's OR field
    (now removed); this version covers the equivalent invariant for Step 1
    itself: once collected, no editable OpenRouter input is rendered
    anywhere on /onboarding/.
    """
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    r = client.get("/onboarding/")
    assert r.status_code == 200
    # Masked summary present instead of input.
    assert _VALID_OR[-4:] in r.text
    assert "Change keys" in r.text
    # No editable OpenRouter input anywhere on the page.
    assert 'name="openrouter_api_key"' not in r.text
