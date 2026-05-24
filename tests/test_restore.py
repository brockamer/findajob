"""Tests for the restore module (#841)."""

from __future__ import annotations

import io
import stat
import tarfile
import tempfile
from pathlib import Path

from findajob.web.restore import MAX_UPLOAD_BYTES, restore_from_tarball, validate_tarball
from tests.conftest import init_test_db


def _make_real_db_bytes() -> bytes:
    """Create a real SQLite DB with the production schema applied."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        init_test_db(Path(tmp.name))
        return Path(tmp.name).read_bytes()


def _make_tarball(**overrides: bytes | None) -> bytes:
    """Build a minimal valid backup tarball. Override or omit entries via kwargs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        entries: dict[str, bytes] = {
            "state/data/pipeline.db": _make_real_db_bytes(),
            "state/data/.onboarding-complete": b"2026-05-24T00:00:00Z\n",
            "state/data/.env": b"OPENROUTER_API_KEY=sk-test\n",
            "state/config/prefilter_rules.yaml": b"rules: []\n",
        }
        entries.update({k: v for k, v in overrides.items() if v is not None})
        for k, v in overrides.items():
            if v is None and k in entries:
                del entries[k]

        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestValidateTarball:
    def test_valid_tarball(self) -> None:
        assert validate_tarball(_make_tarball()) is None

    def test_rejects_non_gzip(self) -> None:
        err = validate_tarball(b"not a tarball")
        assert err is not None
        assert "valid" in err.lower()

    def test_rejects_missing_db(self) -> None:
        raw = _make_tarball(**{"state/data/pipeline.db": None})
        err = validate_tarball(raw)
        assert err is not None
        assert "pipeline.db" in err

    def test_rejects_missing_sentinel(self) -> None:
        raw = _make_tarball(**{"state/data/.onboarding-complete": None})
        err = validate_tarball(raw)
        assert err is not None
        assert ".onboarding-complete" in err

    def test_rejects_no_state_prefix(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"hello"
            info = tarfile.TarInfo(name="data/pipeline.db")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        err = validate_tarball(buf.getvalue())
        assert err is not None
        assert "state/" in err

    def test_rejects_oversized(self) -> None:
        err = validate_tarball(b"x" * (MAX_UPLOAD_BYTES + 1))
        assert err is not None
        assert "large" in err.lower()


class TestRestoreFromTarball:
    def test_happy_path(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()
        (base / "data").mkdir()

        raw = _make_tarball()
        result = restore_from_tarball(raw, base)

        assert result.success is True
        assert (base / "data" / "pipeline.db").exists()
        assert (base / "data" / ".onboarding-complete").exists()
        assert (base / "data" / ".env").exists()
        assert (base / "config" / "prefilter_rules.yaml").exists()

    def test_env_permissions_fixed(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()

        raw = _make_tarball()
        result = restore_from_tarball(raw, base)
        assert result.success is True

        env_path = base / "data" / ".env"
        mode = env_path.stat().st_mode
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR
        assert not (mode & stat.S_IRGRP)
        assert not (mode & stat.S_IROTH)

    def test_replaces_existing_state(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        data = base / "data"
        data.mkdir(parents=True)
        (data / "old_file.txt").write_text("old content")

        raw = _make_tarball()
        result = restore_from_tarball(raw, base)
        assert result.success is True
        assert not (data / "old_file.txt").exists()
        assert (data / "pipeline.db").exists()

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in [
                ("state/data/pipeline.db", b"db"),
                ("state/data/.onboarding-complete", b"ts\n"),
                ("state/../../../etc/passwd", b"pwned"),
            ]:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

        result = restore_from_tarball(buf.getvalue(), base)
        assert result.success is False
        assert "unsafe" in (result.error or "").lower()

    def test_staging_and_rollback_cleaned_up(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        base.mkdir()

        raw = _make_tarball()
        result = restore_from_tarball(raw, base)
        assert result.success is True

        remaining = [p.name for p in base.iterdir() if p.name.startswith(".restore-")]
        assert remaining == []
