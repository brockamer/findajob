"""SQLite WAL-checkpoint helper for the stack exporter (#816).

The exporter must produce a tarball whose embedded ``pipeline.db`` is
self-contained — no pending WAL data, no shm sidecar racing with future
writers. The operator stops the docker stack before export (so nothing
holds the DB), then this helper runs ``PRAGMA wal_checkpoint(TRUNCATE)``
and asserts the ``.sqlite-wal`` sidecar is empty (or absent).

A non-empty WAL after TRUNCATE means either the stack wasn't actually
stopped (a writer is still holding the WAL) or the DB is in a state we
don't understand. Either way, the export must abort loudly — silently
including a dirty WAL in the tarball is exactly the data-corruption
mode the AC ("fail loudly on dirty WAL — no silent partial export")
calls out.
"""

from __future__ import annotations

from pathlib import Path

from findajob.db import connect


class DirtyWalError(RuntimeError):
    """Raised when a WAL checkpoint did not truncate the sidecar to empty."""


def checkpoint(db_path: Path) -> None:
    """Run ``PRAGMA wal_checkpoint(TRUNCATE)`` against the DB.

    Does not verify the result — call :func:`verify_wal_empty` afterward.
    Split so the verify step is testable in isolation without faking sqlite.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def verify_wal_empty(db_path: Path) -> None:
    """Raise :class:`DirtyWalError` if the ``.sqlite-wal`` sidecar exists
    and is non-empty. Treats absent and zero-byte as both clean."""
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    wal_file = db_path.with_suffix(db_path.suffix + "-wal")
    if wal_file.exists() and wal_file.stat().st_size > 0:
        raise DirtyWalError(
            f"WAL sidecar non-empty after checkpoint: {wal_file} "
            f"({wal_file.stat().st_size} bytes). Is the source stack actually stopped?"
        )


def checkpoint_and_verify(db_path: Path) -> None:
    """Checkpoint then verify. The composed call the exporter uses."""
    checkpoint(db_path)
    verify_wal_empty(db_path)
