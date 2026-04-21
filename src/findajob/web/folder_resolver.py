"""Fingerprint → folder path resolution with traversal guards.

Pure helper: no FastAPI, no I/O beyond filesystem existence checks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def resolve_folder(fingerprint: str, db: sqlite3.Connection, companies_root: Path) -> Path | None:
    """Resolve a fingerprint to its prep-folder path on disk.

    Returns None if:
      - fingerprint is not in the jobs table
      - jobs.prep_folder_path is NULL or empty
      - the resolved path does not exist on disk
      - the resolved path escapes companies_root (path-traversal guard)
    """
    row = db.execute("SELECT prep_folder_path FROM jobs WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if row is None:
        return None
    raw = row["prep_folder_path"] if isinstance(row, sqlite3.Row) else row[0]
    if not raw:
        return None

    candidate = Path(raw).resolve()
    root = companies_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None

    if not candidate.is_dir():
        return None
    return candidate
