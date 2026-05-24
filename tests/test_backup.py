"""Tests for the backup module (#841)."""

from __future__ import annotations

import io
import sqlite3
import tarfile
from pathlib import Path

import pytest

from findajob.web.backup import _should_exclude, stream_backup_tarball


@pytest.fixture
def state_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal state directory tree and return (base, db_path)."""
    base = tmp_path / "state_root"
    data = base / "data"
    data.mkdir(parents=True)

    db_path = data / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO test_table VALUES (1, 'hello')")
    conn.commit()
    conn.close()

    (data / ".env").write_text("OPENROUTER_API_KEY=sk-test\n")
    (data / ".onboarding-complete").write_text("2026-05-24T00:00:00Z\n")

    config = base / "config"
    config.mkdir()
    (config / "prefilter_rules.yaml").write_text("rules: []\n")

    cc = base / "candidate_context"
    cc.mkdir()
    (cc / "profile.md").write_text("# Profile\n")

    companies = base / "companies"
    companies.mkdir()
    applied = companies / "_applied"
    applied.mkdir()
    (applied / "SomeCorp_Eng_2026-01-01_120000").mkdir()

    stale = companies / ".stale"
    stale.mkdir()
    (stale / "old_dup").write_text("stale")

    logs = base / "logs"
    logs.mkdir()
    (logs / "pipeline.jsonl").write_text('{"event": "test"}\n')

    (data / "pipeline.db-wal").write_text("wal data")
    (data / "pipeline.db-shm").write_text("shm data")
    (data / "something.bak").write_text("backup")

    return base, db_path


class TestShouldExclude:
    def test_excludes_stale_dir(self) -> None:
        assert _should_exclude("companies/.stale") is True

    def test_excludes_wal(self) -> None:
        assert _should_exclude("data/pipeline.db-wal") is True

    def test_excludes_shm(self) -> None:
        assert _should_exclude("data/pipeline.db-shm") is True

    def test_excludes_bak(self) -> None:
        assert _should_exclude("data/something.bak") is True

    def test_allows_normal_files(self) -> None:
        assert _should_exclude("data/.env") is False
        assert _should_exclude("config/prefilter_rules.yaml") is False


class TestStreamBackupTarball:
    def test_produces_valid_tarball(self, state_tree: tuple[Path, Path]) -> None:
        base, db_path = state_tree
        chunks = list(stream_backup_tarball(base, db_path))
        raw = b"".join(chunks)
        assert len(raw) > 0

        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            names = tar.getnames()
            assert "state/data/pipeline.db" in names
            assert "state/data/.env" in names
            assert "state/data/.onboarding-complete" in names
            assert "state/config/prefilter_rules.yaml" in names
            assert "state/candidate_context/profile.md" in names
            assert "state/logs/pipeline.jsonl" in names

    def test_excludes_transient_files(self, state_tree: tuple[Path, Path]) -> None:
        base, db_path = state_tree
        raw = b"".join(stream_backup_tarball(base, db_path))

        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            names = tar.getnames()
            assert not any("pipeline.db-wal" in n for n in names)
            assert not any("pipeline.db-shm" in n for n in names)
            assert not any(".stale" in n for n in names)
            assert not any(".bak" in n for n in names)

    def test_db_is_consistent_backup(self, state_tree: tuple[Path, Path]) -> None:
        base, db_path = state_tree
        raw = b"".join(stream_backup_tarball(base, db_path))

        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            db_member = tar.getmember("state/data/pipeline.db")
            f = tar.extractfile(db_member)
            assert f is not None
            db_bytes = f.read()

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            Path(tmp.name).write_bytes(db_bytes)
            conn = sqlite3.connect(tmp.name)
            rows = conn.execute("SELECT val FROM test_table WHERE id=1").fetchone()
            conn.close()
            assert rows == ("hello",)

    def test_tarball_top_level_is_state(self, state_tree: tuple[Path, Path]) -> None:
        base, db_path = state_tree
        raw = b"".join(stream_backup_tarball(base, db_path))

        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for name in tar.getnames():
                assert name.startswith("state/") or name == "state", f"Entry {name!r} does not have state/ prefix"
