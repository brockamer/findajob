"""HTTP Basic Auth middleware on the findajob web UI (#327).

Canary tests: if anyone reorders middleware in `app.py` and the auth gate
stops firing, the no-creds → 401 assertions fail. Removing or weakening
these tests requires a deliberate review.
"""

from __future__ import annotations

import base64
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _make_app(tmp_path: Path):
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, "
        "stage TEXT, fit_score REAL, created_at TEXT, stage_updated TEXT)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return create_app(companies_root=companies, db_path=db, base_root=tmp_path)


def _basic(user: str, pw: str) -> str:
    raw = f"{user}:{pw}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


@pytest.fixture
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "tester")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "s3cret-token-xyz")
    app = _make_app(tmp_path)
    return TestClient(app)


@pytest.fixture
def open_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("FINDAJOB_AUTH_USER", raising=False)
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    app = _make_app(tmp_path)
    return TestClient(app)


def test_protected_route_without_creds_returns_401(auth_client: TestClient) -> None:
    r = auth_client.get("/", follow_redirects=False)
    assert r.status_code == 401
    challenge = r.headers.get("www-authenticate", "")
    assert challenge.lower().startswith("basic ")
    assert 'realm="findajob"' in challenge


def test_protected_route_with_correct_creds_passes(auth_client: TestClient) -> None:
    r = auth_client.get(
        "/",
        headers={"Authorization": _basic("tester", "s3cret-token-xyz")},
        follow_redirects=False,
    )
    assert r.status_code == 200


def test_protected_route_with_wrong_password_returns_401(auth_client: TestClient) -> None:
    r = auth_client.get(
        "/",
        headers={"Authorization": _basic("tester", "wrong")},
        follow_redirects=False,
    )
    assert r.status_code == 401


def test_protected_route_with_wrong_user_returns_401(auth_client: TestClient) -> None:
    r = auth_client.get(
        "/",
        headers={"Authorization": _basic("attacker", "s3cret-token-xyz")},
        follow_redirects=False,
    )
    assert r.status_code == 401


def test_healthz_is_allowlisted(auth_client: TestClient) -> None:
    r = auth_client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_static_is_allowlisted(auth_client: TestClient) -> None:
    r = auth_client.get("/static/app.css")
    assert r.status_code != 401


def test_favicon_is_allowlisted(auth_client: TestClient) -> None:
    r = auth_client.get("/favicon.ico")
    assert r.status_code == 200


def test_no_env_vars_means_no_auth(open_client: TestClient) -> None:
    r = open_client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_only_user_env_set_means_no_auth_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Partial config fail-opens but MUST emit a WARNING (silent fail-open is the foot-gun)."""
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "tester")
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    with caplog.at_level(logging.WARNING, logger="findajob.web.auth"):
        app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert any(
        rec.levelno == logging.WARNING
        and "DISABLED" in rec.message
        and "FINDAJOB_AUTH_USER" in rec.message
        and "FINDAJOB_AUTH_PASS" in rec.message
        for rec in caplog.records
    ), f"expected partial-config WARNING; got {[(r.levelname, r.message) for r in caplog.records]}"


def test_only_pass_env_set_means_no_auth_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("FINDAJOB_AUTH_USER", raising=False)
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "lonely")
    with caplog.at_level(logging.WARNING, logger="findajob.web.auth"):
        app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert any(rec.levelno == logging.WARNING and "DISABLED" in rec.message for rec in caplog.records)


def test_install_logs_enabled_when_both_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operator should be able to grep startup logs for the actual auth state."""
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "tester")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "s3cret-token-xyz")
    with caplog.at_level(logging.INFO, logger="findajob.web.auth"):
        _make_app(tmp_path)
    assert any("ENABLED" in rec.message and rec.levelno == logging.INFO for rec in caplog.records)


def test_install_logs_disabled_when_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("FINDAJOB_AUTH_USER", raising=False)
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    with caplog.at_level(logging.INFO, logger="findajob.web.auth"):
        _make_app(tmp_path)
    assert any("DISABLED (no env vars set" in rec.message and rec.levelno == logging.INFO for rec in caplog.records)


def test_empty_string_env_vars_mean_no_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "")
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_whitespace_only_env_vars_mean_no_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Some Docker Compose versions don't strip inline `# comment` text from
    env_file lines, so `FINDAJOB_AUTH_USER=   # leave empty to disable auth`
    can land here as whitespace-only (or worse). The auth gate must treat
    both empty and whitespace-only as "disabled" or testers get locked out
    behind random characters they didn't set.
    """
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "    ")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "\t  \n")
    app = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_malformed_authorization_header_returns_401(auth_client: TestClient) -> None:
    r = auth_client.get("/", headers={"Authorization": "Bearer xyz"}, follow_redirects=False)
    assert r.status_code == 401


def test_garbage_base64_returns_401(auth_client: TestClient) -> None:
    r = auth_client.get(
        "/",
        headers={"Authorization": "Basic !!!not-base64!!!"},
        follow_redirects=False,
    )
    assert r.status_code == 401


def test_basic_with_no_colon_returns_401(auth_client: TestClient) -> None:
    raw = base64.b64encode(b"nocolonhere").decode("ascii")
    r = auth_client.get("/", headers={"Authorization": f"Basic {raw}"}, follow_redirects=False)
    assert r.status_code == 401
