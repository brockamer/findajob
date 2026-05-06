"""Tests for findajob.cost_rollups SQL helpers."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Fresh init_db.py-bootstrapped connection."""
    db_path = tmp_path / "pipeline.db"
    subprocess.run(
        [sys.executable, "scripts/init_db.py", str(db_path)],
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _insert_calibration(
    conn: sqlite3.Connection,
    *,
    polled_at: str | None = None,
    credits_remaining_usd: float = 67.86,
    multiplier: float = 1.3,
    multiplier_clamped: int = 0,
    poll_status: str = "ok",
) -> None:
    conn.execute(
        """INSERT INTO cost_calibration
           (polled_at, credits_total_usd, credits_used_usd, credits_remaining_usd,
            onboarding_total_usd, pipeline_actual_usd, heuristic_sum_usd,
            multiplier, multiplier_clamped, poll_status)
           VALUES (?, 100.0, 32.14, ?, 4.50, 27.64, 21.26, ?, ?, ?)""",
        (
            polled_at or _utc_now().strftime("%Y-%m-%d %H:%M:%S"),
            credits_remaining_usd,
            multiplier,
            multiplier_clamped,
            poll_status,
        ),
    )
    conn.commit()


def test_current_calibration_returns_latest_row(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import current_calibration

    _insert_calibration(db, credits_remaining_usd=50.0, multiplier=1.2)
    _insert_calibration(db, credits_remaining_usd=42.0, multiplier=1.4)

    cal = current_calibration(db)
    assert cal is not None
    assert cal.credits_remaining_usd == 42.0
    assert cal.multiplier == 1.4
    assert cal.poll_status == "ok"
    assert cal.multiplier_clamped is False


def test_current_calibration_returns_none_when_empty(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import current_calibration

    assert current_calibration(db) is None


def test_current_calibration_marks_stale_after_one_hour(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import current_calibration

    old = (_utc_now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_calibration(db, polled_at=old)

    cal = current_calibration(db)
    assert cal is not None
    assert cal.poll_status == "stale"


def test_current_calibration_reports_clamping(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import current_calibration

    _insert_calibration(db, multiplier_clamped=1)

    cal = current_calibration(db)
    assert cal is not None
    assert cal.multiplier_clamped is True
