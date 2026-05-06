"""Verify init_db.py creates cost_calibration table with expected columns."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path


def test_init_db_creates_cost_calibration(tmp_path: Path) -> None:
    """Running init_db.py against a fresh DB creates cost_calibration table."""
    db_path = tmp_path / "pipeline.db"
    subprocess.run(
        [sys.executable, "scripts/init_db.py", str(db_path)],
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )

    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cost_calibration)").fetchall()]
    conn.close()

    expected = {
        "id",
        "polled_at",
        "credits_total_usd",
        "credits_used_usd",
        "credits_remaining_usd",
        "onboarding_total_usd",
        "pipeline_actual_usd",
        "heuristic_sum_usd",
        "multiplier",
        "multiplier_clamped",
        "poll_status",
        "error_message",
    }
    assert expected.issubset(set(cols)), f"missing cols: {expected - set(cols)}"


def test_init_db_creates_cost_calibration_index(tmp_path: Path) -> None:
    db_path = tmp_path / "pipeline.db"
    subprocess.run(
        [sys.executable, "scripts/init_db.py", str(db_path)],
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    conn = sqlite3.connect(str(db_path))
    indexes = [r[1] for r in conn.execute("PRAGMA index_list(cost_calibration)").fetchall()]
    conn.close()
    assert "idx_cost_calibration_polled_at" in indexes
