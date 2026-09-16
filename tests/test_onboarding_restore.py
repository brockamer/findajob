"""Tests for /onboarding/restore/ route (#841)."""

from __future__ import annotations

import io
import sqlite3
import tarfile
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app
from tests.conftest import init_test_db


def _make_real_db_bytes() -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        init_test_db(Path(tmp.name))
        return Path(tmp.name).read_bytes()


def _make_valid_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        entries = {
            "state/data/pipeline.db": _make_real_db_bytes(),
            "state/data/.onboarding-complete": b"2026-05-24T00:00:00Z\n",
            "state/data/.env": b"OPENROUTER_API_KEY=sk-test\n",
            "state/config/prefilter_rules.yaml": b"rules: []\n",
        }
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _setup_token(client: TestClient) -> str:
    """The token POST /onboarding/restore/upload requires while auth is unset."""
    return client.app.state.setup_token  # type: ignore[attr-defined]


@pytest.fixture
def fresh_base(tmp_path: Path) -> Path:
    """Factory-clean base — no sentinel, no data."""
    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE _placeholder (id INTEGER)")
    conn.close()
    return tmp_path


@pytest.fixture
def onboarded_base(tmp_path: Path) -> Path:
    """Already-onboarded base — sentinel present."""
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (id INTEGER)")
    conn.close()
    (tmp_path / "data" / ".onboarding-complete").write_text("2026-05-24T00:00:00Z\n")
    return tmp_path


def _client(base: Path) -> TestClient:
    app = create_app(
        companies_root=base / "companies",
        db_path=base / "data" / "pipeline.db",
        base_root=base,
    )
    return TestClient(app, follow_redirects=False)


class TestGetRestorePage:
    def test_renders_on_fresh_stack(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        r = client.get("/onboarding/restore/")
        assert r.status_code == 200
        assert "Restore from backup" in r.text

    def test_renders_on_onboarded_stack(self, onboarded_base: Path) -> None:
        client = _client(onboarded_base)
        r = client.get("/onboarding/restore/")
        assert r.status_code == 200
        assert "already set up" in r.text.lower() or "Restore from backup" in r.text


class TestPostRestore:
    def test_fresh_stack_restore(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        tarball = _make_valid_tarball()
        r = client.post(
            "/onboarding/restore/upload",
            data={"setup_token": _setup_token(client)},
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(tarball), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/board/dashboard"
        assert (fresh_base / "data" / ".onboarding-complete").exists()
        assert (fresh_base / "data" / ".env").exists()

    def test_onboarded_stack_requires_confirm(self, onboarded_base: Path) -> None:
        client = _client(onboarded_base)
        tarball = _make_valid_tarball()
        r = client.post(
            "/onboarding/restore/upload",
            data={"setup_token": _setup_token(client)},
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(tarball), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 409
        assert "already" in r.text.lower() or "overwrite" in r.text.lower()

    def test_onboarded_stack_with_confirm(self, onboarded_base: Path) -> None:
        client = _client(onboarded_base)
        tarball = _make_valid_tarball()
        r = client.post(
            "/onboarding/restore/upload",
            data={"confirm_overwrite": "yes", "setup_token": _setup_token(client)},
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(tarball), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 303

    def test_invalid_tarball_returns_400(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        r = client.post(
            "/onboarding/restore/upload",
            data={"setup_token": _setup_token(client)},
            files={"backup_tarball": ("bad.tar.gz", io.BytesIO(b"not a tarball"), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "valid" in r.text.lower()

    def test_back_to_onboarding_link(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        r = client.get("/onboarding/restore/")
        assert "/onboarding/" in r.text


class TestSetupTokenGate:
    """POST /onboarding/restore/upload must carry the same gate as /onboarding/auth.

    Restore replaces data/, config/, candidate_context/, companies/ and logs/
    wholesale and can plant a data/.env whose credentials go live on the next
    restart.  While Basic Auth is unset -- the bootstrap window, or a
    perimeter-only deployment -- an unauthenticated caller previously reached it
    with nothing but a forged confirm_overwrite field.
    """

    def test_upload_without_token_is_refused(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        r = client.post(
            "/onboarding/restore/upload",
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(_make_valid_tarball()), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 401
        assert "setup token" in r.text.lower()
        assert not (fresh_base / "data" / ".onboarding-complete").exists()

    def test_upload_with_wrong_token_is_refused(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        r = client.post(
            "/onboarding/restore/upload",
            data={"setup_token": "not-the-token"},
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(_make_valid_tarball()), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 401
        assert not (fresh_base / "data" / ".onboarding-complete").exists()

    def test_confirm_overwrite_alone_cannot_bypass_the_gate(self, onboarded_base: Path) -> None:
        """The overwrite confirm is a form field, not an authorisation."""
        client = _client(onboarded_base)
        r = client.post(
            "/onboarding/restore/upload",
            data={"confirm_overwrite": "yes"},
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(_make_valid_tarball()), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 401

    def test_gate_is_skipped_once_basic_auth_is_active(self, fresh_base: Path) -> None:
        """With auth configured the middleware already gates every request."""
        client = _client(fresh_base)
        client.app.state.auth_user = "operator"  # type: ignore[attr-defined]
        client.app.state.auth_pass = "secret"  # type: ignore[attr-defined]
        # The middleware now guards every request, so authenticate as the
        # operator would.  No setup token is supplied: that is the point.
        r = client.post(
            "/onboarding/restore/upload",
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(_make_valid_tarball()), "application/gzip")},
            auth=("operator", "secret"),
            follow_redirects=False,
        )
        assert r.status_code == 303

    def test_form_shows_the_token_field_only_when_auth_is_unset(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        assert 'name="setup_token"' in client.get("/onboarding/restore/").text
        client.app.state.auth_user = "operator"  # type: ignore[attr-defined]
        client.app.state.auth_pass = "secret"  # type: ignore[attr-defined]
        assert 'name="setup_token"' not in client.get("/onboarding/restore/").text
