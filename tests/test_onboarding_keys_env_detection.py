"""Tests for env-var detection at /onboarding/ Step 1 (#676).

When OPENROUTER_API_KEY / RAPIDAPI_KEY are present in the container env
(Fly secrets, compose env_file, etc.) AND no SQLite credentials row
exists yet, /onboarding/ renders a "Use detected keys" affordance
instead of the empty form. Clicking it POSTs to
/onboarding/keys/use-detected, which re-reads os.environ server-side
(no secrets in hidden form inputs), runs the same validate + smoke +
persist chain as /onboarding/keys, and lands the user in the same
keys-collected state.

?manual=1 on /onboarding/ suppresses detection for that render — used
by the Override link on the detected UI and by the reset redirect.
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
    # Default: no env vars set. Individual tests opt in via monkeypatch.setenv.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
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


# ── Index rendering: branch selection ───────────────────────────────────


def test_index_no_env_vars_renders_empty_form(client: TestClient) -> None:
    r = client.get("/onboarding/")
    assert r.status_code == 200
    # Empty form: input fields present, no detected-keys affordance.
    assert "Use detected keys" not in r.text
    assert 'name="openrouter_api_key"' in r.text


def test_index_env_vars_present_renders_detected_ui(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    monkeypatch.setenv("RAPIDAPI_KEY", _VALID_RAPID)
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "Use detected keys" in r.text
    # Masked tail rendered for both keys.
    assert _VALID_OR[-4:] in r.text
    assert _VALID_RAPID[-4:] in r.text
    # Empty form NOT rendered (the detected UI replaces it).
    assert 'name="openrouter_api_key"' not in r.text


def test_index_env_only_openrouter_renders_detected_ui_with_rapid_blank(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    # RAPIDAPI_KEY intentionally unset
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "Use detected keys" in r.text
    assert _VALID_OR[-4:] in r.text
    # Inactive-RapidAPI hint surfaces in the detected branch (parity with
    # the keys-collected branch).
    assert "LinkedIn / Indeed search inactive" in r.text


def test_index_env_vars_with_sqlite_row_renders_keys_collected(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Persist via the manual form first (SQLite wins).
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    assert _stored_credentials(base_root) == (_VALID_OR, None)
    # Now stage env vars; index should still show the SQLite-derived masked summary.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-tester-different-from-sqlite")
    monkeypatch.setenv("RAPIDAPI_KEY", _VALID_RAPID)
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "Change keys" in r.text
    assert "Use detected keys" not in r.text


def test_index_manual_query_param_suppresses_detection(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    r = client.get("/onboarding/?manual=1")
    assert r.status_code == 200
    assert "Use detected keys" not in r.text
    assert 'name="openrouter_api_key"' in r.text


# ── POST /onboarding/keys/use-detected ──────────────────────────────────


def test_use_detected_persists_env_keys(client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    monkeypatch.setenv("RAPIDAPI_KEY", _VALID_RAPID)
    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/"
    assert _row_count(base_root) == 1
    assert _stored_credentials(base_root) == (_VALID_OR, _VALID_RAPID)


def test_use_detected_persists_when_only_openrouter_in_env(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code == 303
    assert _stored_credentials(base_root) == (_VALID_OR, None)


def test_use_detected_with_no_env_vars_redirects_to_manual_form(client: TestClient, base_root: Path) -> None:
    # No env vars set; the button shouldn't have rendered in the first
    # place, but a direct POST (or a stale tab) must degrade safely.
    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/?manual=1"
    assert _row_count(base_root) == 0


def test_use_detected_skips_smoke_when_no_rapidapi_in_env(
    client: TestClient,
    base_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RapidAPI smoke must not run when RAPIDAPI_KEY isn't set in env —
    parity with the manual form's blank-input behavior (#689)."""
    import findajob.web.routes.onboarding as ob_routes

    smoke_calls: list[str] = []

    def _tripwire(key: str) -> tuple[bool, str | None]:
        smoke_calls.append(key)
        return (True, None)

    monkeypatch.setattr(ob_routes, "verify_rapidapi_key", _tripwire)
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    # RAPIDAPI_KEY intentionally unset

    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code == 303
    assert smoke_calls == []
    assert _stored_credentials(base_root) == (_VALID_OR, None)


def test_use_detected_format_failure_does_not_persist(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-valid-openrouter-key")
    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code == 400
    # Error wording identifies the env-var source so the operator knows
    # to fix OPENROUTER_API_KEY (Fly secret or data/.env), not paste in
    # the form. Substring chosen to dodge HTML-entity escaping of the
    # apostrophe in "container's".
    assert "OPENROUTER_API_KEY" in r.text
    assert "format validation" in r.text
    assert _row_count(base_root) == 0


def test_use_detected_smoke_failure_does_not_persist(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Passes format (sk-or-v1- prefix) but fails the stub smoke check.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-rejected-by-smoke")
    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code == 400
    assert "stale" in r.text or "rotate" in r.text.lower()
    assert _row_count(base_root) == 0


def test_use_detected_rapidapi_smoke_failure_does_not_persist(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    # Passes format, fails RapidAPI smoke stub.
    monkeypatch.setenv("RAPIDAPI_KEY", "syntactically-valid-but-not-live")
    r = client.post("/onboarding/keys/use-detected")
    assert r.status_code == 400
    assert "RapidAPI" in r.text
    assert _row_count(base_root) == 0


def test_use_detected_then_index_renders_keys_collected(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After successful use-detected POST, the index renders the
    masked-summary keys-collected branch (Step 2 enabled), not the
    detected-keys UI — same terminal state as the manual save path."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    monkeypatch.setenv("RAPIDAPI_KEY", _VALID_RAPID)
    client.post("/onboarding/keys/use-detected")
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "Change keys" in r.text
    assert "Use detected keys" not in r.text
    # Step 2 enabled — no "save your API keys" gate text.
    assert "Save your API keys above before continuing" not in r.text


# ── Reset path: redirects to /onboarding/?manual=1 ──────────────────────


def test_reset_redirects_with_manual_query_param(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After reset, the user expects the empty form — not the detected
    UI from env vars they implicitly opted out of by clicking reset."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _VALID_OR)
    client.post("/onboarding/keys", data={"openrouter_api_key": _VALID_OR})
    r = client.post("/onboarding/keys", data={"reset": "1"})
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/?manual=1"

    # Following the redirect: empty form shown, NOT detected UI.
    r2 = client.get("/onboarding/?manual=1")
    assert r2.status_code == 200
    assert "Use detected keys" not in r2.text
    assert 'name="openrouter_api_key"' in r2.text
