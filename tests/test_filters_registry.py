"""Tests for web/filters/registry.py — source enum and per-tab ColumnSpec lists."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from findajob.db.migrate import apply_pending
from findajob.web.filters import registry as reg


def test_jsearch_in_source_values() -> None:
    """'jsearch' must appear in _SOURCE_VALUES for the board filter dropdown. (#408 / closes #310)"""
    assert "jsearch" in reg._SOURCE_VALUES


def test_stage_values_are_all_legal_db_stages(tmp_path: Path) -> None:
    """Every _STAGE_VALUES entry must be insertable as jobs.stage under the
    real CHECK constraint (#894).

    The Stage filter offers these as enum options; the SQL WHERE clause feeds
    them straight to the DB. A value the CHECK constraint rejects (e.g. the
    'withdrew' drift — the DB only ever stores 'withdrawn') matches zero rows
    and silently breaks the filter. Validating against the migrated schema —
    not a hand-rolled fixture — is the oracle that was missing.
    """
    db_path = tmp_path / "pipeline.db"
    with sqlite3.connect(db_path) as conn:
        apply_pending(conn)
        for i, stage in enumerate(reg._STAGE_VALUES):
            conn.execute(
                "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"id{i}", f"fp{i}", "http://example.test", "T", "C", "src", stage),
            )
