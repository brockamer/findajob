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

_VALID_OR = "sk-or-v1-tester-fake-test-1234"
_VALID_RAPID = "fakeRapidApiTesterKey1234567890abcdef"


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config").mkdir()

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


@pytest.fixture
def client(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Stub the live OpenRouter + RapidAPI smoke checks — tests must not make real network calls.
    import findajob.web.routes.onboarding as ob_routes

    monkeypatch.setattr(
        ob_routes,
        "verify_openrouter_key",
        lambda key: (True, None) if "tester" in key or "valid" in key else (False, "key invalid"),
    )
    monkeypatch.setattr(
        ob_routes,
        "verify_rapidapi_key",
        lambda key: (True, None) if "fakeRapid" in key or "tester" in key else (False, "RapidAPI key invalid"),
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


def _stored_credentials(base_root: Path) -> tuple[str | None, str | None] | None:
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        row = conn.execute(
            """SELECT tester_openrouter_key, tester_rapidapi_key
               FROM onboarding_sessions ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        return row if row else None
    finally:
        conn.close()


def test_post_both_valid_creates_one_row(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": _VALID_OR,
            "rapidapi_key": _VALID_RAPID,
        },
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/"
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (_VALID_OR, _VALID_RAPID)


def test_post_only_openrouter_stores_optional_fields_null(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": _VALID_OR},
    )
    assert r.status_code == 303
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (_VALID_OR, None)


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
        data={"openrouter_api_key": second_or},
    )
    assert r.status_code == 303
    assert _row_count(base_root) == 1
    # Second submission's values win; UPDATE semantic preserved.
    creds = _stored_credentials(base_root)
    assert creds == (second_or, None)


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
    assert _stored_credentials(base_root) == (_VALID_OR, None)


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
    assert _stored_credentials(base_root) == (_VALID_OR, None)
    r = client.post("/onboarding/keys", data={"reset": "1"})
    assert r.status_code == 303
    # Row stays; just credentials columns are cleared (chat history would also
    # remain if any existed — Task 4's "Change keys" semantic per plan).
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (None, None)


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


def test_post_openrouter_key_in_rapidapi_field_rejected_at_format(client: TestClient, base_root: Path) -> None:
    """#689: pasting an sk-or-v1- key in the RapidAPI field must fail format
    validation BEFORE any smoke call — caught by the validator's cross-paste check."""
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": _VALID_OR,
            "rapidapi_key": "sk-or-v1-cross-paste-mistake",
        },
    )
    assert r.status_code == 400
    assert "OpenRouter" in r.text  # error message identifies the mistake
    assert _row_count(base_root) == 0


def test_post_rapidapi_smoke_failure_does_not_write_db(client: TestClient, base_root: Path) -> None:
    """#689: a syntactically valid RapidAPI key that fails the live smoke
    check must NOT be persisted; route returns 400 with the smoke error."""
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": _VALID_OR,
            # passes format (printable, no whitespace, not sk-or-v1-) but fails the
            # fixture's smoke stub (no "fakeRapid"/"tester" substring)
            "rapidapi_key": "syntactically-valid-but-not-live",
        },
    )
    assert r.status_code == 400
    assert "RapidAPI" in r.text or "rejected" in r.text.lower()
    assert _row_count(base_root) == 0


def test_post_blank_rapidapi_skips_smoke_and_persists(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#689: when RapidAPI field is blank, the smoke check must NOT run —
    RapidAPI is optional at Step 1 and blank is a valid choice."""
    import findajob.web.routes.onboarding as ob_routes

    smoke_calls: list[str] = []

    def _tripwire(key: str) -> tuple[bool, str | None]:
        smoke_calls.append(key)
        return (True, None)

    monkeypatch.setattr(ob_routes, "verify_rapidapi_key", _tripwire)

    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": _VALID_OR, "rapidapi_key": ""},
    )
    assert r.status_code == 303
    assert smoke_calls == []  # blank value skipped the live check
    assert _stored_credentials(base_root) == (_VALID_OR, None)


def test_post_preserves_rapidapi_on_failure(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": "garbage",
            "rapidapi_key": _VALID_RAPID,
        },
    )
    assert r.status_code == 400
    # OpenRouter is intentionally NOT preserved; RapidAPI is.
    assert _VALID_RAPID in r.text


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
