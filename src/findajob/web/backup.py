"""#841: Core backup logic — streaming tarball creation with sqlite3 .backup.

Produces a gzipped tarball whose top-level directory is ``state/`` to match
the contract in ``docs/operations/restore.md``. On disk paths are BASE-relative;
the ``state/`` prefix is added during archival so the tarball is portable across
Docker (BASE=/app) and Fly (BASE=/app/state) deployments.

The SQLite database is dumped via the online backup API to a temp file (not a raw
file copy) to avoid WAL inconsistency. The temp file is cleaned up after archival.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
import threading
from collections.abc import Generator
from pathlib import Path

from findajob.db import connect as db_connect

_TARBALL_PREFIX = "state"

_EXCLUDED_NAMES = frozenset(
    {
        ".stale",
        "pipeline.db-shm",
        "pipeline.db-wal",
    }
)

_EXCLUDED_SUFFIXES = (".bak",)

_STATE_DIRS = ("data", "config", "candidate_context", "companies", "logs")


def _should_exclude(path: str) -> bool:
    """True if a path component matches an excluded name or suffix."""
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if part in _EXCLUDED_NAMES:
            return True
        if any(part.endswith(s) for s in _EXCLUDED_SUFFIXES):
            return True
    return False


def _tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Filter callback for tarfile.add — exclude transient files, add prefix.

    Called after tarfile has already set tarinfo.name to dirname-relative
    paths (because we pass arcname=dirname to tar.add).
    """
    if _should_exclude(tarinfo.name):
        return None
    if tarinfo.name == "data/pipeline.db":
        return None
    tarinfo.name = f"{_TARBALL_PREFIX}/{tarinfo.name}"
    return tarinfo


def _backup_db(db_path: Path, dest: Path) -> None:
    """Online backup of a live SQLite database to a destination file."""
    src_conn = db_connect(db_path, ro=True)
    try:
        dst_conn = db_connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def stream_backup_tarball(base: Path, db_path: Path) -> Generator[bytes, None, None]:
    """Yield gzipped tar chunks containing the stack's state.

    The tarball top-level is ``state/`` per the documented contract.
    SQLite DB is dumped via the backup API to a temp file first.
    All other state directories are added directly from disk.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as tmp:
        tmp_db = Path(tmp.name)
        _backup_db(db_path, tmp_db)

        read_fd, write_fd = os.pipe()
        write_error: list[Exception] = []

        def _write_tar() -> None:
            try:
                with os.fdopen(write_fd, "wb") as wf:
                    with tarfile.open(fileobj=wf, mode="w|gz") as tar:
                        tar.add(
                            str(tmp_db),
                            arcname=f"{_TARBALL_PREFIX}/data/pipeline.db",
                        )
                        for dirname in _STATE_DIRS:
                            dirpath = base / dirname
                            if not dirpath.is_dir():
                                continue
                            tar.add(
                                str(dirpath),
                                arcname=dirname,
                                recursive=True,
                                filter=_tar_filter,
                            )
            except Exception as exc:
                write_error.append(exc)

        t = threading.Thread(target=_write_tar, daemon=True)
        t.start()

        try:
            with os.fdopen(read_fd, "rb") as rf:
                while True:
                    chunk = rf.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            t.join(timeout=60)

        if write_error:
            raise write_error[0]
