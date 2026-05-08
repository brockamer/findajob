"""#514 — legacy schema baseline test.

Locks the contract: a v0.10.0-shape SQLite fixture, run through the
current ``scripts/init_db.py`` followed by ``migrate_schema()`` (the same
arc production stacks execute on container restart), produces a schema
identical to the fresh-init baseline snapshot from #513. Any divergence
means the legacy upgrade path is broken.

The deployment arc being mirrored:

1. Container entrypoint runs ``scripts/init_db.py`` — handles inline
   ALTER TABLE additions on ``jobs``, drops ``cost_calibration`` if
   present, and runs ``CREATE TABLE/INDEX IF NOT EXISTS`` for everything
   else.
2. FastAPI startup hook calls
   ``findajob.onboarding.session_store.migrate_schema(conn)`` — adds
   layered ``onboarding_sessions`` columns and drops removed ones.

This test executes both steps in order, then compares to the fresh
baseline.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from findajob.onboarding.session_store import migrate_schema
from tests._schema_introspect import diff_summary, introspect
from tests.fixtures._legacy_v0_10_setup import write_v0_10_0_db

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_DB_SCRIPT = REPO_ROOT / "scripts" / "init_db.py"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "schema_baseline_fresh.json"


def test_legacy_v0_10_0_upgrades_to_fresh_baseline(tmp_path: Path) -> None:
    if not SNAPSHOT_PATH.exists():
        pytest.fail(
            "fresh baseline snapshot is missing — generate it first via "
            "`UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest "
            "tests/test_schema_baseline_fresh.py`"
        )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    db_path = tmp_path / "legacy.db"
    write_v0_10_0_db(db_path)

    subprocess.run(
        [sys.executable, str(INIT_DB_SCRIPT), str(db_path)],
        check=True,
        capture_output=True,
    )

    conn = sqlite3.connect(db_path)
    try:
        migrate_schema(conn)
        actual = introspect(conn)
    finally:
        conn.close()

    if actual != expected:
        diff = diff_summary(actual, expected)
        pytest.fail(
            "v0.10.0 → current upgrade path produced a schema that diverges "
            "from the fresh baseline. Either a migration is missing from "
            "init_db.py / migrate_schema(), or the legacy fixture in "
            "tests/fixtures/_legacy_v0_10_setup.py has drifted from the actual "
            "v0.10.0 shape (verify with `git show v0.10.0:scripts/init_db.py`).\n\n"
            f"{diff}"
        )
