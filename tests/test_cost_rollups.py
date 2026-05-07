"""Tests for findajob.cost_rollups SQL helpers."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
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


def test_per_job_cost_sums_non_null_rows(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import per_job_cost

    _insert_job_with_costs(
        db,
        "job-a",
        [("briefing", 0.10), ("resume_tailor", 0.50), ("cover_letter", None)],
    )

    cost = per_job_cost(db, "job-a")
    assert cost == pytest.approx(0.60, rel=1e-3)


def test_per_job_cost_returns_none_for_all_nulls(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import per_job_cost

    _insert_job_with_costs(db, "job-b", [("briefing", None), ("score", None)])

    assert per_job_cost(db, "job-b") is None


def test_per_job_breakdown_groups_by_operation(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import per_job_breakdown

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


def test_projected_monthly_scales_7d(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import projected_monthly

    # 3 inserts in the current week (Sunday anchor ≤ 6 days ago) summing to $7.00.
    # The projected_monthly filter is logged_at >= datetime('now', '-7 days'),
    # and Sunday-of-current-week is always within that window.
    # projection = 7.0 × 30/7 = 30.0
    _insert_cost_log(db, weeks_ago=0, cost=2.10)
    _insert_cost_log(db, weeks_ago=0, cost=3.50)
    _insert_cost_log(db, weeks_ago=0, cost=1.40)
    assert projected_monthly(db) == pytest.approx(30.0, rel=1e-3)


def test_projected_monthly_none_when_no_recent_spend(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import projected_monthly

    assert projected_monthly(db) is None


def test_spend_this_month_sums_current_month_rows(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import spend_this_month

    # Three rows in the current calendar month (datetime('now') default) summing to $4.20.
    _insert_job_with_costs(
        db,
        "job-spend",
        [("briefing", 1.50), ("resume_tailor", 2.00), ("cover_letter", 0.70)],
    )
    assert spend_this_month(db) == pytest.approx(4.20, rel=1e-3)


def test_spend_this_month_zero_when_empty(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import spend_this_month

    assert spend_this_month(db) == 0.0
