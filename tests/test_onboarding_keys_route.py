"""Tests for POST /onboarding/keys and the Step 1 index states.

Step 1 validates the keys (format, then live smoke check) and saves them to
data/.env and os.environ. It writes nothing to onboarding_sessions.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding.keys import current_keys
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


def _env_text(base_root: Path) -> str:
    path = base_root / "data" / ".env"
    return path.read_text() if path.exists() else ""


def _db_key_rows(base_root: Path) -> list[tuple[str | None, str | None, str | None]]:
    conn = sqlite3.connect(base_root / "data" / "pipeline.db")
    try:
        return conn.execute(
            "SELECT user_openrouter_key, user_rapidapi_key, user_gemini_api_key FROM onboarding_sessions"
        ).fetchall()
    finally:
        conn.close()


def _assert_nothing_saved(base_root: Path) -> None:
    assert _env_text(base_root) == ""
    assert current_keys() == ("", "", "")
    assert _db_key_rows(base_root) == []


def test_post_both_valid_saves_to_env_not_db(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": _VALID_OR, "rapidapi_key": _VALID_RAPID},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/"
    text = _env_text(base_root)
    assert f"OPENROUTER_API_KEY={_VALID_OR}\n" in text
    assert f"RAPIDAPI_KEY={_VALID_RAPID}\n" in text
    assert stat.S_IMODE((base_root / "data" / ".env").stat().st_mode) == 0o600
    assert current_keys()[:2] == (_VALID_OR, _VALID_RAPID)
    # No session row is created and no key column is written.
    assert _db_key_rows(base_root) == []


def test_post_only_openrouter_leaves_optional_keys_unset(client: TestClient, base_root: Path) -> None:
    r = client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    assert r.status_code == 303
    assert current_keys() == (_VALID_OR, "", "")
    assert "RAPIDAPI_KEY" not in _env_text(base_root)


def test_post_blank_optional_keeps_saved_value(client: TestClient, base_root: Path) -> None:
    (base_root / "data" / ".env").write_text("GEMINI_API_KEY=saved-gemini-key\n")
    os.environ["GEMINI_API_KEY"] = "saved-gemini-key"
    r = client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR, "gemini_api_key": ""})
    assert r.status_code == 303
    assert "GEMINI_API_KEY=saved-gemini-key\n" in _env_text(base_root)
    assert current_keys()[2] == "saved-gemini-key"


def test_post_malformed_openrouter_saves_nothing(client: TestClient, base_root: Path) -> None:
    r = client.post("/onboarding/keys", data={"openrouter_api_key": "not-a-valid-key"})
    assert r.status_code == 400
    assert "Couldn't save your keys" in r.text or "openrouter" in r.text.lower()
    _assert_nothing_saved(base_root)


def test_post_smoke_failure_saves_nothing(client: TestClient, base_root: Path) -> None:
    r = client.post("/onboarding/keys", data={"openrouter_api_key": "sk-or-v1-rejected-by-smoke"})
    assert r.status_code == 400
    assert "rejected" in r.text.lower() or "verify" in r.text.lower()
    _assert_nothing_saved(base_root)


def test_post_twice_second_values_win(client: TestClient, base_root: Path) -> None:
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR, "rapidapi_key": _VALID_RAPID})
    second_or = "sk-or-v1-tester-different-key-xyz"
    r = client.post("/onboarding/keys", data={"openrouter_api_key": second_or})
    assert r.status_code == 303
    text = _env_text(base_root)
    assert text.count("OPENROUTER_API_KEY=") == 1
    assert f"OPENROUTER_API_KEY={second_or}\n" in text
    # Blank RapidAPI on the second save keeps the first value (D3).
    assert f"RAPIDAPI_KEY={_VALID_RAPID}\n" in text


def test_post_fail_fail_success_saves_once(client: TestClient, base_root: Path) -> None:
    assert client.post("/onboarding/keys", data={"openrouter_api_key": "garbage"}).status_code == 400
    assert client.post("/onboarding/keys", data={"openrouter_api_key": "sk-or-v1-rejected"}).status_code == 400
    _assert_nothing_saved(base_root)
    r3 = client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    assert r3.status_code == 303
    assert current_keys()[0] == _VALID_OR
    assert _db_key_rows(base_root) == []


def test_post_reset_redirects_to_manual_form_and_keeps_keys(client: TestClient, base_root: Path) -> None:
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    before = _env_text(base_root)
    r = client.post("/onboarding/keys", data={"reset": "1"})
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/?manual=1"
    assert _env_text(base_root) == before
    assert current_keys()[0] == _VALID_OR


def test_post_gemini_with_line_break_is_rejected_without_writing(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={"openrouter_api_key": _VALID_OR, "gemini_api_key": "gem\nINJECTED=1"},
    )
    assert r.status_code == 400
    assert "Couldn't save your keys" in r.text
    _assert_nothing_saved(base_root)


def test_index_with_no_keys_renders_empty_form(client: TestClient) -> None:
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert 'name="openrouter_api_key"' in r.text
    assert "Save your API keys above before continuing" in r.text


def test_index_with_placeholder_openrouter_value_renders_empty_form(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Docker install seeds data/.env from data/.env.example, which holds a
    # placeholder; compose loads it into the web process. It is not a saved key.
    monkeypatch.setenv("OPENROUTER_API_KEY", "your_key_here")
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert 'name="openrouter_api_key"' in r.text
    assert "***here" not in r.text
    assert "Save your API keys above before continuing" in r.text


def test_index_with_keys_in_environment_shows_keys_on_file(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env-AB12")
    monkeypatch.setenv("RAPIDAPI_KEY", "rapid-from-env-CD34")
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "***AB12" in r.text
    assert "***CD34" in r.text
    assert "Change keys" in r.text
    assert 'name="openrouter_api_key"' not in r.text
    assert "Use detected keys" not in r.text
    assert "Save your API keys above before continuing" not in r.text


def test_manual_param_shows_empty_form_even_with_keys_on_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env-AB12")
    r = client.get("/onboarding/?manual=1")
    assert r.status_code == 200
    assert 'name="openrouter_api_key"' in r.text
    assert "***AB12" not in r.text


def test_use_detected_route_is_gone(client: TestClient) -> None:
    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code in (404, 405)


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


def test_post_invalid_rapidapi_does_not_write_db(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/keys",
        data={
            "openrouter_api_key": _VALID_OR,
            "rapidapi_key": "key with spaces in it",  # whitespace forbidden
        },
    )
    assert r.status_code == 400
    _assert_nothing_saved(base_root)


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
    _assert_nothing_saved(base_root)


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
    _assert_nothing_saved(base_root)


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
    assert current_keys() == (_VALID_OR, "", "")


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
    than asking the user for keys they've never seen this UI ask for."""
    sentinel = base_root / "data" / ".onboarding-complete"
    sentinel.write_text("2026-04-29T00:00:00Z\n")
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "You've already onboarded" in r.text
    assert "No OpenRouter key is saved for this findajob" in r.text


def test_already_onboarded_hint_absent_on_manual_form_when_key_saved(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Change keys (?manual=1) on an onboarded instance with a saved key must not
    claim that no key is saved."""
    (base_root / "data" / ".onboarding-complete").write_text("2026-04-29T00:00:00Z\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-from-env-AB12")
    r = client.get("/onboarding/?manual=1")
    assert r.status_code == 200
    assert 'name="openrouter_api_key"' in r.text  # the empty form still renders
    assert "No OpenRouter key is saved for this findajob" not in r.text


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
    """If the user has already used Step 1, they're past the
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
