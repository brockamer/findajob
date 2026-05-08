"""#513 — fresh schema baseline test.

Locks the contract: a fresh ``init_db.py`` against an empty tmpdir produces
a schema (tables, indexes, columns, FKs, CHECK constraints, partial-index
WHERE clauses) that exactly matches the committed JSON snapshot at
``tests/fixtures/schema_baseline_fresh.json``. Drift fails the test and
forces an explicit migration decision in the same PR.

To regenerate the snapshot after a legitimate schema change::

    UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_schema_baseline_fresh.py

Re-run without the env var to confirm the new snapshot is committed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests._schema_introspect import diff_summary, introspect

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_DB_SCRIPT = REPO_ROOT / "scripts" / "init_db.py"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "schema_baseline_fresh.json"


def _fresh_schema_snapshot(tmp_path: Path) -> dict:
    """Run ``scripts/init_db.py`` against an empty tmpdir and introspect."""
    db_path = tmp_path / "fresh.db"
    subprocess.run(
        [sys.executable, str(INIT_DB_SCRIPT), str(db_path)],
        check=True,
        capture_output=True,
    )
    conn = sqlite3.connect(db_path)
    try:
        return introspect(conn)
    finally:
        conn.close()


def test_fresh_init_matches_snapshot(tmp_path: Path) -> None:
    actual = _fresh_schema_snapshot(tmp_path)

    if os.environ.get("UPDATE_SCHEMA_SNAPSHOT") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"snapshot regenerated at {SNAPSHOT_PATH.relative_to(REPO_ROOT)}")

    if not SNAPSHOT_PATH.exists():
        pytest.fail(
            f"baseline snapshot missing at {SNAPSHOT_PATH.relative_to(REPO_ROOT)}; "
            "regenerate with `UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest "
            "tests/test_schema_baseline_fresh.py`"
        )

    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    if actual != expected:
        diff = diff_summary(actual, expected)
        pytest.fail(
            "schema diverged from baseline snapshot. If this change is "
            "intentional, regenerate the snapshot with "
            "`UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest "
            "tests/test_schema_baseline_fresh.py` and commit it in the same PR.\n\n"
            f"{diff}"
        )
