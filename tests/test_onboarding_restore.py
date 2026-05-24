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
            data={"confirm_overwrite": "yes"},
            files={"backup_tarball": ("backup.tar.gz", io.BytesIO(tarball), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 303

    def test_invalid_tarball_returns_400(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        r = client.post(
            "/onboarding/restore/upload",
            files={"backup_tarball": ("bad.tar.gz", io.BytesIO(b"not a tarball"), "application/gzip")},
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "valid" in r.text.lower()

    def test_back_to_onboarding_link(self, fresh_base: Path) -> None:
        client = _client(fresh_base)
        r = client.get("/onboarding/restore/")
        assert "/onboarding/" in r.text
