"""Tests for POST /onboarding/auth (#895 — in-app auth credential setup).

The route collects username + password, writes to data/.env + app.state,
and the dynamic BasicAuthMiddleware starts enforcing auth on the next
request.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app
from findajob.web.auth_env import is_auth_configured


@pytest.fixture(autouse=True)
def _ensure_auth_env_cleanup():
    """write_auth_credentials sets os.environ directly; clean up after each test."""
    yield
    os.environ.pop("FINDAJOB_AUTH_USER", None)
    os.environ.pop("FINDAJOB_AUTH_PASS", None)


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
    monkeypatch.delenv("FINDAJOB_AUTH_USER", raising=False)
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _setup_token(client: TestClient) -> str:
    """Pull the setup token off the running app's state for tests."""
    return client.app.state.setup_token  # type: ignore[attr-defined]


def test_auth_form_visible_when_no_creds(client: TestClient) -> None:
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert "Set your login password" in r.text


def test_auth_form_hidden_when_creds_set(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "admin")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "testpassword123")
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    c = TestClient(app, follow_redirects=False)
    r = c.get(
        "/onboarding/",
        headers={"Authorization": "Basic " + __import__("base64").b64encode(b"admin:testpassword123").decode()},
    )
    assert r.status_code == 200
    assert "Set your login password" not in r.text


def test_auth_post_success_redirects(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/auth",
        data={
            "setup_token": _setup_token(client),
            "auth_username": "myuser",
            "auth_password": "mypassword123",
            "auth_password_confirm": "mypassword123",
        },
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/"
    assert is_auth_configured(base_root)


def test_auth_post_writes_to_env_file(client: TestClient, base_root: Path) -> None:
    client.post(
        "/onboarding/auth",
        data={
            "setup_token": _setup_token(client),
            "auth_username": "testuser",
            "auth_password": "securepass123",
            "auth_password_confirm": "securepass123",
        },
    )
    env_content = (base_root / "data" / ".env").read_text()
    assert "FINDAJOB_AUTH_USER=testuser" in env_content
    assert "FINDAJOB_AUTH_PASS=securepass123" in env_content


def test_auth_activates_middleware_immediately(
    client: TestClient, base_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from findajob.onboarding import mark_complete

    mark_complete(base_root)

    r = client.get("/board/dashboard", follow_redirects=False)
    assert r.status_code == 200

    client.post(
        "/onboarding/auth",
        data={
            "setup_token": _setup_token(client),
            "auth_username": "guard",
            "auth_password": "testpass1234",
            "auth_password_confirm": "testpass1234",
        },
    )

    r = client.get("/board/dashboard", follow_redirects=False)
    assert r.status_code == 401


def test_auth_password_mismatch_returns_error(client: TestClient) -> None:
    r = client.post(
        "/onboarding/auth",
        data={
            "setup_token": _setup_token(client),
            "auth_username": "user",
            "auth_password": "password123",
            "auth_password_confirm": "different123",
        },
    )
    assert r.status_code == 400
    assert "do not match" in r.text


def test_auth_password_too_short_returns_error(client: TestClient) -> None:
    r = client.post(
        "/onboarding/auth",
        data={
            "setup_token": _setup_token(client),
            "auth_username": "user",
            "auth_password": "short",
            "auth_password_confirm": "short",
        },
    )
    assert r.status_code == 400
    assert "at least 8" in r.text


def test_auth_empty_username_returns_error(client: TestClient) -> None:
    r = client.post(
        "/onboarding/auth",
        data={
            "setup_token": _setup_token(client),
            "auth_username": "",
            "auth_password": "password123",
            "auth_password_confirm": "password123",
        },
    )
    assert r.status_code == 400
    assert "required" in r.text


def test_auth_env_preserves_existing_keys(client: TestClient, base_root: Path) -> None:
    env_path = base_root / "data" / ".env"
    env_path.write_text("OPENROUTER_API_KEY=sk-or-existing\nRAPIDAPI_KEY=rapid-existing\n")

    client.post(
        "/onboarding/auth",
        data={
            "setup_token": _setup_token(client),
            "auth_username": "newuser",
            "auth_password": "newpass12345",
            "auth_password_confirm": "newpass12345",
        },
    )

    content = env_path.read_text()
    assert "OPENROUTER_API_KEY=sk-or-existing" in content
    assert "RAPIDAPI_KEY=rapid-existing" in content
    assert "FINDAJOB_AUTH_USER=newuser" in content
    assert "FINDAJOB_AUTH_PASS=newpass12345" in content


def test_setup_token_generated_when_no_creds(client: TestClient) -> None:
    """A fresh instance with no creds must have a setup token on app.state."""
    assert _setup_token(client)


def test_setup_token_absent_when_creds_set(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An instance with creds already set must NOT generate a setup token."""
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "preset")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "preset-password-12345")
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    assert not getattr(app.state, "setup_token", None)


def test_auth_post_without_setup_token_rejected(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/auth",
        data={
            "auth_username": "attacker",
            "auth_password": "evilpassword",
            "auth_password_confirm": "evilpassword",
        },
    )
    assert r.status_code == 400
    assert "Setup token" in r.text
    assert not is_auth_configured(base_root)


def test_auth_post_with_wrong_setup_token_rejected(client: TestClient, base_root: Path) -> None:
    r = client.post(
        "/onboarding/auth",
        data={
            "setup_token": "wrong-token-value",
            "auth_username": "attacker",
            "auth_password": "evilpassword",
            "auth_password_confirm": "evilpassword",
        },
    )
    assert r.status_code == 400
    assert "Setup token" in r.text
    assert not is_auth_configured(base_root)


def test_setup_token_cleared_after_successful_auth(client: TestClient) -> None:
    """Single-use: token must be cleared after a successful auth setup."""
    token = _setup_token(client)
    assert token
    client.post(
        "/onboarding/auth",
        data={
            "setup_token": token,
            "auth_username": "ops",
            "auth_password": "secure-pass-789",
            "auth_password_confirm": "secure-pass-789",
        },
    )
    assert not _setup_token(client)


def test_setup_token_form_field_shown_when_required(client: TestClient) -> None:
    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert 'name="setup_token"' in r.text
    assert "FINDAJOB_SETUP_TOKEN" in r.text


def test_partial_config_still_requires_setup_token(base_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: when only USER or PASS is set (typo'd config), the
    middleware fails open AND a setup token must still gate /onboarding/auth.
    Earlier code skipped token generation in this branch — drive-by squat hole.
    """
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "onlyhalf")
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    assert app.state.setup_token, "partial-config branch must generate a setup token"

    c = TestClient(app, follow_redirects=False)
    r = c.post(
        "/onboarding/auth",
        data={
            "auth_username": "attacker",
            "auth_password": "squat-password",
            "auth_password_confirm": "squat-password",
        },
    )
    assert r.status_code == 400
    assert "Setup token" in r.text
    assert not is_auth_configured(base_root)
