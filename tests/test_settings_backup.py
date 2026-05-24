"""Tests for /settings/backup/ route (#841)."""

from __future__ import annotations

import io
import sqlite3
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO jobs VALUES (1, 'Test Engineer')")
    conn.commit()
    conn.close()
    (tmp_path / "data" / ".onboarding-complete").write_text("2026-05-24T00:00:00Z\n")
    (tmp_path / "data" / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
    (tmp_path / "config" / "prefilter_rules.yaml").write_text("rules: []\n")
    (tmp_path / "logs" / "pipeline.jsonl").write_text('{"event":"test"}\n')
    return tmp_path


@pytest.fixture
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


class TestGetBackupPage:
    def test_renders(self, client: TestClient) -> None:
        r = client.get("/settings/backup/")
        assert r.status_code == 200
        assert "Download backup tarball" in r.text

    def test_shows_secrets_warning(self, client: TestClient) -> None:
        r = client.get("/settings/backup/")
        assert "API keys and personal data" in r.text


class TestPostDownload:
    def test_streams_valid_tarball(self, client: TestClient) -> None:
        r = client.post("/settings/backup/download")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/gzip"
        assert "findajob-backup-" in r.headers["content-disposition"]

        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
            names = tar.getnames()
            assert "state/data/pipeline.db" in names
            assert "state/data/.env" in names
            assert "state/config/prefilter_rules.yaml" in names

    def test_db_in_tarball_is_valid(self, client: TestClient) -> None:
        r = client.post("/settings/backup/download")
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
            f = tar.extractfile("state/data/pipeline.db")
            assert f is not None
            db_bytes = f.read()

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            Path(tmp.name).write_bytes(db_bytes)
            conn = sqlite3.connect(tmp.name)
            row = conn.execute("SELECT title FROM jobs WHERE id=1").fetchone()
            conn.close()
            assert row == ("Test Engineer",)
