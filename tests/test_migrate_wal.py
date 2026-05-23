"""WAL-checkpoint helper unit tests (#816)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob.migrate import wal


def _make_wal_db(db_path: Path) -> None:
    """Open a fresh DB in WAL mode and write a row so a WAL file exists."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


def test_checkpoint_truncates_wal(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    _make_wal_db(db)
    # Re-open and write so the -wal sidecar definitely exists at checkpoint time.
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO t VALUES (2)")
    conn.commit()
    conn.close()

    wal.checkpoint_and_verify(db)

    wal_file = db.with_suffix(db.suffix + "-wal")
    # After TRUNCATE checkpoint, the -wal sidecar may be absent or 0-byte.
    if wal_file.exists():
        assert wal_file.stat().st_size == 0, f"WAL not empty after checkpoint: {wal_file.stat().st_size} bytes"


def test_checkpoint_raises_when_db_missing(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        wal.checkpoint_and_verify(db)


def test_verify_raises_when_wal_sidecar_nonempty(tmp_path: Path) -> None:
    """If a concurrent writer holds the WAL, checkpoint(TRUNCATE) returns
    'busy' and the sidecar stays non-empty. ``verify_wal_empty`` must
    surface this as a loud error — that's the 'dirty WAL' AC case."""
    db = tmp_path / "pipeline.db"
    _make_wal_db(db)
    wal_file = db.with_suffix(db.suffix + "-wal")
    wal_file.write_bytes(b"\x00" * 64)  # synthetic dirty WAL
    with pytest.raises(wal.DirtyWalError):
        wal.verify_wal_empty(db)


def test_verify_passes_when_wal_sidecar_absent(tmp_path: Path) -> None:
    """A clean checkpoint may leave the -wal sidecar entirely absent
    (TRUNCATE may unlink). That's the 'clean' case, not an error."""
    db = tmp_path / "pipeline.db"
    db.write_bytes(b"\x00" * 100)  # fake DB; verify_wal_empty doesn't read it
    wal.verify_wal_empty(db)  # no raise


def test_verify_passes_when_wal_sidecar_zero_bytes(tmp_path: Path) -> None:
    """Zero-byte -wal also counts as clean."""
    db = tmp_path / "pipeline.db"
    db.write_bytes(b"\x00" * 100)
    (tmp_path / "pipeline.db-wal").write_bytes(b"")
    wal.verify_wal_empty(db)  # no raise
