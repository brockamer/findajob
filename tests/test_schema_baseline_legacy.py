"""#514 — legacy schema baseline test.

Locks the contract: a v0.10.0-shape SQLite fixture, run through the
current ``scripts/init_db.py`` (which under M5 invokes
``findajob.db.migrate.apply_pending``), produces a schema identical to
the fresh-init baseline snapshot from #513. Any divergence means the
legacy upgrade path is broken.

The deployment arc being mirrored:

The container entrypoint (``ops/entrypoint.sh``) runs ``scripts/init_db.py``,
which calls ``findajob.db.migrate.apply_pending``. The runner's heuristic
detects the v0.10.0 shape (missing columns, missing tables, presence of
``cost_calibration``, presence of ``tester_google_key``), bridges it to
the equilibrium via :func:`findajob.db.migrate._bridge_legacy_to_v1`,
then stamps ``_meta.schema_version=1``. This test asserts the bridged
shape matches the fresh baseline.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

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
        actual = introspect(conn)
    finally:
        conn.close()

    if actual != expected:
        diff = diff_summary(actual, expected)
        pytest.fail(
            "v0.10.0 → current upgrade path produced a schema that diverges "
            "from the fresh baseline. Either a migration is missing from "
            "migrations/0001_initial.sql, the M5 heuristic backfill in "
            "findajob.db.migrate._bridge_legacy_to_v1 is incomplete, or the "
            "legacy fixture in tests/fixtures/_legacy_v0_10_setup.py has "
            "drifted from the actual v0.10.0 shape (verify with "
            "`git show v0.10.0:scripts/init_db.py`).\n\n"
            f"{diff}"
        )
