"""Helpers for building admin-stack test fixtures programmatically.

We do not commit binary SQLite files; tests build them inline so the
fixture intent is visible in the test source.

Schema construction routes through ``findajob.db.migrate.apply_pending``
(the M5 #552 runner) rather than hand-written SQL — the fixture matches
the production schema exactly, eliminating the fixture-vs-production
drift pattern that motivated the runner. After ``apply_pending`` runs
on a fresh DB, ``_meta.schema_version`` lands at the head version and
both ``jobs`` and ``background_tasks`` (M6 #554) exist with their full
column sets.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from findajob.db import connect
from findajob.db.migrate import apply_pending


def build_pipeline_db(
    db_path: Path,
    *,
    rows: list[dict] | None = None,
    bg_tasks: list[dict] | None = None,
) -> None:
    """Build a pipeline.db at the canonical apply_pending schema.

    ``rows`` is a list of dicts representing ``jobs`` rows. Required
    NOT NULL columns (``fingerprint``, ``url``, ``title``, ``company``,
    ``source``) are auto-synthesized from ``id`` if not provided. The
    ``prep_started_at`` key (used by older tests) maps to
    ``stage_updated`` for backwards compatibility.

    ``bg_tasks`` is a list of dicts representing ``background_tasks``
    rows. Required keys: ``id``, ``job_id``, ``kind``, ``started_at``.
    Optional: ``status`` (default ``'running'``), ``finished_at``,
    ``error_message``, ``pid``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path, timeout=5.0)
    try:
        apply_pending(conn)
        conn.row_factory = sqlite3.Row

        for r in rows or []:
            jid = r["id"]
            conn.execute(
                "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, stage_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    jid,
                    r.get("fingerprint") or f"f-{jid}",
                    r.get("url") or f"http://test.example/{jid}",
                    r.get("title") or "Test",
                    r.get("company") or "Test Co",
                    r.get("source") or "test",
                    r["stage"],
                    r.get("stage_updated") or r.get("prep_started_at"),
                ),
            )

        for t in bg_tasks or []:
            conn.execute(
                "INSERT INTO background_tasks "
                "(id, job_id, kind, started_at, finished_at, status, error_message, pid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t["id"],
                    t["job_id"],
                    t["kind"],
                    t["started_at"],
                    t.get("finished_at"),
                    t.get("status", "running"),
                    t.get("error_message"),
                    t.get("pid"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def build_pipeline_jsonl(jsonl_path: Path, events: list[dict]) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def write_corrupt_db(db_path: Path) -> None:
    """Write garbage that will fail to open as SQLite."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite database")
