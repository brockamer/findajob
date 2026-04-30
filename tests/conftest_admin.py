"""Helpers for building admin-stack test fixtures programmatically.

We do not commit binary SQLite files; tests build them inline so the
fixture intent is visible in the test source.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_JOBS_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    flagged_at TEXT,
    prep_started_at TEXT
);
"""


def build_pipeline_db(
    db_path: Path,
    *,
    rows: list[dict] | None = None,
) -> None:
    """Build a minimal pipeline.db with just the columns stack_health reads.

    `rows` is a list of dicts with keys: id, stage, prep_started_at (ISO 8601 UTC).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_JOBS_SCHEMA)
    for r in rows or []:
        conn.execute(
            "INSERT INTO jobs (id, stage, prep_started_at) VALUES (?, ?, ?)",
            (r["id"], r["stage"], r.get("prep_started_at")),
        )
    conn.commit()
    conn.close()


def build_pipeline_jsonl(jsonl_path: Path, events: list[dict]) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def write_corrupt_db(db_path: Path) -> None:
    """Write garbage that will fail to open as SQLite."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite database")
