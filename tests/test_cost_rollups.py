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


def _insert_job_with_costs(
    conn: sqlite3.Connection,
    job_id: str,
    rows: list[tuple[str, float | None]],
) -> None:
    """Seed jobs.id and one cost_log row per (operation, cost_usd) tuple."""
    conn.execute(
        """INSERT INTO jobs
           (id, fingerprint, title, company, location, source, url, stage)
           VALUES (?, ?, 'Title', 'Company', 'Loc', 'src', '', 'applied')""",
        (job_id, f"fp-{job_id}"),
    )
    for op, cost in rows:
        conn.execute(
            """INSERT INTO cost_log (job_id, operation, model, cost_usd, success)
               VALUES (?, ?, 'google/gemini-3-flash-preview', ?, 1)""",
            (job_id, op, cost),
        )
    conn.commit()


def test_per_job_cost_excludes_nulls(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import per_job_cost

    _insert_calibration(db, multiplier=1.3)
    _insert_job_with_costs(
        db,
        "job-a",
        [("briefing", 0.10), ("resume_tailor", 0.50), ("cover_letter", None)],
    )

    cost = per_job_cost(db, "job-a")
    assert cost is not None
    # (0.10 + 0.50) × 1.3 = 0.78
    assert cost == pytest.approx(0.78, rel=1e-3)


def test_per_job_cost_returns_none_for_all_nulls(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import per_job_cost

    _insert_calibration(db, multiplier=1.3)
    _insert_job_with_costs(db, "job-b", [("briefing", None), ("score", None)])

    assert per_job_cost(db, "job-b") is None


def test_per_job_cost_returns_none_when_no_calibration(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import per_job_cost

    _insert_job_with_costs(db, "job-c", [("briefing", 0.10)])

    # No calibration → multiplier defaults to 1.0; raw heuristic shown.
    cost = per_job_cost(db, "job-c")
    assert cost == pytest.approx(0.10, rel=1e-3)


def test_per_job_breakdown_groups_by_operation(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import per_job_breakdown

    _insert_calibration(db, multiplier=1.0)
    _insert_job_with_costs(
        db,
        "job-d",
        [
            ("briefing", 0.20),
            ("resume_tailor", 0.30),
            ("cover_letter", 0.25),
            ("briefing", 0.05),  # second briefing call, e.g. regenerate
        ],
    )

    rows = per_job_breakdown(db, "job-d")
    by_op = {r.operation: r.cost_usd for r in rows}
    assert by_op["briefing"] == pytest.approx(0.25, rel=1e-3)  # 0.20 + 0.05
    assert by_op["resume_tailor"] == pytest.approx(0.30, rel=1e-3)
    assert by_op["cover_letter"] == pytest.approx(0.25, rel=1e-3)


def _insert_cost_log(
    conn: sqlite3.Connection,
    *,
    weeks_ago: int,
    cost: float,
    operation: str = "briefing",
) -> None:
    """Insert one cost_log row at a deterministic position in the week N back.

    Anchors at the Sunday of the current UTC week minus weeks_ago×7 days.
    The week-bucket SQL uses Sunday as the week-start, so a Sunday-of-week-N
    row always maps to the same Sunday anchor regardless of which day the
    test runs on. strftime('%w', 'now') returns 0 for Sunday, so
    '-0 days' is safe and correct.
    """
    conn.execute(
        """INSERT INTO cost_log
           (job_id, operation, model, cost_usd, success, logged_at)
           VALUES (NULL, ?, 'google/gemini-3-flash-preview', ?, 1,
                   datetime('now',
                            '-' || strftime('%w', 'now') || ' days',
                            '-' || ? || ' days'))""",
        (operation, cost, weeks_ago * 7),
    )
    conn.commit()


def test_weekly_spend_returns_n_weeks_oldest_first(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import weekly_spend

    _insert_calibration(db, multiplier=1.0)
    _insert_cost_log(db, weeks_ago=0, cost=1.00)  # current week
    _insert_cost_log(db, weeks_ago=1, cost=2.00)  # 1 week ago
    _insert_cost_log(db, weeks_ago=2, cost=3.00)  # 2 weeks ago

    weeks = weekly_spend(db, weeks=4)
    assert len(weeks) == 4
    # Last entry = current week.
    assert weeks[-1].total_usd == pytest.approx(1.00, rel=1e-3)
    # Second-to-last = 1 week ago.
    assert weeks[-2].total_usd == pytest.approx(2.00, rel=1e-3)
    # Third-to-last = 2 weeks ago.
    assert weeks[-3].total_usd == pytest.approx(3.00, rel=1e-3)
    # Fourth-to-last = 3 weeks ago, no inserts → zero.
    assert weeks[-4].total_usd == pytest.approx(0.00, abs=1e-9)


def test_runway_weeks_uses_4wk_average(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import runway_weeks

    _insert_calibration(db, credits_remaining_usd=40.0, multiplier=1.0)
    # $10/wk for 4 weeks → 4-week avg = $10/wk → runway = 40 / 10 = 4
    for w in range(4):
        _insert_cost_log(db, weeks_ago=w, cost=10.0)

    weeks = runway_weeks(db)
    assert weeks == pytest.approx(4.0, rel=0.05)


def test_runway_weeks_none_when_no_history(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import runway_weeks

    _insert_calibration(db, credits_remaining_usd=40.0, multiplier=1.0)
    assert runway_weeks(db) is None


def test_projected_monthly_scales_7d(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import projected_monthly

    _insert_calibration(db, multiplier=1.0)
    # 3 inserts in the current week (Sunday anchor ≤ 6 days ago) summing to $7.00.
    # The projected_monthly filter is logged_at >= datetime('now', '-7 days'),
    # and Sunday-of-current-week is always within that window.
    # projection = 7.0 × 30/7 = 30.0
    _insert_cost_log(db, weeks_ago=0, cost=2.10)
    _insert_cost_log(db, weeks_ago=0, cost=3.50)
    _insert_cost_log(db, weeks_ago=0, cost=1.40)
    assert projected_monthly(db) == pytest.approx(30.0, rel=1e-3)
