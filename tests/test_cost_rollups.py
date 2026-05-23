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
    """Insert one cost_log row at exactly N×7 days before 'now'.

    Shifting by exactly 7N days preserves weekday and time-of-day, so the
    row lands in the Nth-previous week of any tz: "now" is by definition
    in current week (UTC and PT both), "now − 7 days" is in last week,
    etc. Avoids the Sunday-anchored variant which becomes flaky when the
    test runs at UTC times where UTC Sunday and PT Sunday differ
    (roughly UTC 00:00–08:00 on Sundays).
    """
    conn.execute(
        """INSERT INTO cost_log
           (job_id, operation, model, cost_usd, success, logged_at)
           VALUES (NULL, ?, 'google/gemini-3-flash-preview', ?, 1,
                   datetime('now', ?))""",
        (operation, cost, f"-{weeks_ago * 7} days"),
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
    assert weeks[-1].prep_usd == pytest.approx(1.00, rel=1e-3)
    # Second-to-last = 1 week ago.
    assert weeks[-2].prep_usd == pytest.approx(2.00, rel=1e-3)
    # Third-to-last = 2 weeks ago.
    assert weeks[-3].prep_usd == pytest.approx(3.00, rel=1e-3)
    # Fourth-to-last = 3 weeks ago, no inserts → zero.
    assert weeks[-4].prep_usd == pytest.approx(0.00, abs=1e-9)
    # Scoring side stays zero — _insert_cost_log defaults operation='briefing'.
    assert all(w.scoring_usd == pytest.approx(0.0, abs=1e-9) for w in weeks)


def test_weekly_spend_splits_prep_and_scoring(db: sqlite3.Connection) -> None:
    """A score row goes to scoring_usd, non-score to prep_usd, same week."""
    from findajob.cost_rollups import weekly_spend

    _insert_cost_log(db, weeks_ago=0, cost=1.50, operation="briefing")
    _insert_cost_log(db, weeks_ago=0, cost=0.20, operation="score")
    _insert_cost_log(db, weeks_ago=0, cost=0.30, operation="score")

    weeks = weekly_spend(db, weeks=4)
    assert weeks[-1].prep_usd == pytest.approx(1.50, rel=1e-3)
    assert weeks[-1].scoring_usd == pytest.approx(0.50, rel=1e-3)


def test_weekly_spend_tz_shifts_week_boundary(db: sqlite3.Connection) -> None:
    """A row logged 'now' counts toward this week's prep total for any tz.

    Verifies the tz= param wires through to the SQL boundary filter — both
    UTC and America/Los_Angeles should bucket a fresh insert as "current
    week," and the local week_start label differs between the two when the
    operator's PT-anchored week boundary lands on a different calendar
    date than the UTC-anchored one (e.g., late Saturday PT = Sunday UTC).
    """
    from findajob.cost_rollups import weekly_spend

    _insert_cost_log(db, weeks_ago=0, cost=1.00)

    weeks_utc = weekly_spend(db, weeks=2, tz="UTC")
    weeks_pt = weekly_spend(db, weeks=2, tz="America/Los_Angeles")

    # The 'now' insert lands in this-week's bucket regardless of tz.
    assert weeks_utc[-1].prep_usd == pytest.approx(1.00, rel=1e-3)
    assert weeks_pt[-1].prep_usd == pytest.approx(1.00, rel=1e-3)
    # week_start label is a local-tz Sunday date, may differ between tzs.
    assert len(weeks_utc[-1].week_start) == 10  # YYYY-MM-DD
    assert len(weeks_pt[-1].week_start) == 10


def test_projected_monthly_scales_7d(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import projected_monthly

    # 3 prep inserts summing to $7.00 → projection = 7.0 × 30/7 = 30.0
    _insert_cost_log(db, weeks_ago=0, cost=2.10)
    _insert_cost_log(db, weeks_ago=0, cost=3.50)
    _insert_cost_log(db, weeks_ago=0, cost=1.40)
    result = projected_monthly(db)
    assert result.prep_usd == pytest.approx(30.0, rel=1e-3)
    assert result.scoring_usd is None


def test_projected_monthly_splits_prep_and_scoring(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import projected_monthly

    _insert_cost_log(db, weeks_ago=0, cost=0.70, operation="briefing")
    _insert_cost_log(db, weeks_ago=0, cost=1.40, operation="score")
    result = projected_monthly(db)
    assert result.prep_usd == pytest.approx(0.70 * 30.0 / 7.0, rel=1e-3)
    assert result.scoring_usd == pytest.approx(1.40 * 30.0 / 7.0, rel=1e-3)


def test_projected_monthly_none_when_no_recent_spend(db: sqlite3.Connection) -> None:
    from findajob.cost_rollups import projected_monthly

    result = projected_monthly(db)
    assert result.prep_usd is None
    assert result.scoring_usd is None


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


# ---------------------------------------------------------------------------
# #823: TZ-aware monthly reset boundary
# ---------------------------------------------------------------------------


def test_month_anchors_utc_anchors_at_local_midnight_utc_tz() -> None:
    """UTC tz: local-month-start equals UTC-month-start; no offset."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import _month_anchors_utc

    now = datetime(2026, 5, 22, 18, 45, tzinfo=ZoneInfo("UTC"))
    start_utc, end_utc = _month_anchors_utc(now)
    assert start_utc == "2026-05-01 00:00:00"
    assert end_utc == "2026-06-01 00:00:00"


def test_month_anchors_utc_shifts_for_la_pdt() -> None:
    """America/Los_Angeles in May (PDT, UTC-7): local May 1 00:00 PT = May 1 07:00 UTC.

    The June bound also lands in UTC at 07:00, not 00:00 — both endpoints
    shift, so a row at UTC 06:30 on June 1 (= 23:30 PT on May 31) correctly
    counts toward May's spend.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import _month_anchors_utc

    now = datetime(2026, 5, 22, 11, 45, tzinfo=ZoneInfo("America/Los_Angeles"))
    start_utc, end_utc = _month_anchors_utc(now)
    assert start_utc == "2026-05-01 07:00:00"
    assert end_utc == "2026-06-01 07:00:00"


def test_month_anchors_utc_shifts_for_tokyo() -> None:
    """Asia/Tokyo (JST, UTC+9, no DST): local May 1 00:00 JST = April 30 15:00 UTC.

    The local month starts BEFORE the UTC calendar-month boundary by 9 hours.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import _month_anchors_utc

    now = datetime(2026, 5, 22, 14, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    start_utc, end_utc = _month_anchors_utc(now)
    assert start_utc == "2026-04-30 15:00:00"
    assert end_utc == "2026-05-31 15:00:00"


def test_month_anchors_utc_handles_half_hour_offset_kolkata() -> None:
    """Asia/Kolkata (IST, UTC+5:30, no DST): local May 1 00:00 IST = April 30 18:30 UTC.

    Half-hour offset is a non-issue for ``strftime("%Y-%m-%d %H:%M:%S")`` —
    the produced string carries the 30-minute increment cleanly. This is the
    body's explicitly-flagged edge case.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import _month_anchors_utc

    now = datetime(2026, 5, 22, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    start_utc, end_utc = _month_anchors_utc(now)
    assert start_utc == "2026-04-30 18:30:00"
    assert end_utc == "2026-05-31 18:30:00"


def test_month_anchors_utc_handles_dst_transition_la_march() -> None:
    """PT shifts UTC-8 → UTC-7 on 2026-03-08. A 'now' on March 22 is post-DST.

    Local March 1 00:00 PT happened BEFORE the DST transition (still PST, UTC-8),
    so local March 1 00:00 PT = March 1 08:00 UTC. Local April 1 00:00 PT is
    POST-DST (PDT, UTC-7), so April 1 00:00 PT = April 1 07:00 UTC. The interval
    is one hour SHORTER than 31 calendar days — that's the right answer:
    DST "lost" an hour mid-month.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import _month_anchors_utc

    now = datetime(2026, 3, 22, 11, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    start_utc, end_utc = _month_anchors_utc(now)
    assert start_utc == "2026-03-01 08:00:00"  # PST
    assert end_utc == "2026-04-01 07:00:00"  # PDT


def test_month_anchors_utc_handles_year_rollover() -> None:
    """December → January boundary increments the year."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import _month_anchors_utc

    now = datetime(2026, 12, 15, 12, 0, tzinfo=ZoneInfo("UTC"))
    start_utc, end_utc = _month_anchors_utc(now)
    assert start_utc == "2026-12-01 00:00:00"
    assert end_utc == "2027-01-01 00:00:00"


def test_month_anchors_utc_rejects_naive_datetime() -> None:
    """Naive datetime (no tzinfo) is a precondition violation."""
    from datetime import datetime

    from findajob.cost_rollups import _month_anchors_utc

    with pytest.raises(ValueError, match="tz-aware"):
        _month_anchors_utc(datetime(2026, 5, 22, 12, 0))


def test_spend_this_month_pt_includes_row_in_local_month_but_not_utc(db: sqlite3.Connection) -> None:
    """A row logged at 2026-06-01 04:30 UTC = 2026-05-31 21:30 PT.

    In UTC's month-view it belongs to June; in PT's month-view it belongs to May.
    Asserting that `spend_this_month(tz="America/Los_Angeles", now=<June 5 PT>)`
    EXCLUDES this row demonstrates the boundary shift is correct.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import spend_this_month

    # Boundary row: 2026-06-01 04:30 UTC ≡ 2026-05-31 21:30 PT.
    _insert_job_with_costs(db, "job-pt-boundary", [])
    db.execute(
        """INSERT INTO cost_log (job_id, operation, model, cost_usd, success, logged_at)
           VALUES ('job-pt-boundary', 'briefing', 'g/m', 9.99, 1, '2026-06-01 04:30:00')"""
    )
    # And one clearly-June-everywhere row.
    db.execute(
        """INSERT INTO cost_log (job_id, operation, model, cost_usd, success, logged_at)
           VALUES ('job-pt-boundary', 'briefing', 'g/m', 1.00, 1, '2026-06-15 12:00:00')"""
    )
    db.commit()

    now_pt = datetime(2026, 6, 5, 10, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    now_utc = datetime(2026, 6, 5, 17, 0, tzinfo=ZoneInfo("UTC"))

    # PT view of June 2026: only the mid-June row counts ($1.00).
    assert spend_this_month(db, tz="America/Los_Angeles", now=now_pt) == pytest.approx(1.00, rel=1e-3)
    # UTC view of June 2026: both rows count ($10.99).
    assert spend_this_month(db, tz="UTC", now=now_utc) == pytest.approx(10.99, rel=1e-3)


def test_spend_this_month_tokyo_includes_row_in_local_month_but_not_utc(db: sqlite3.Connection) -> None:
    """JST is ahead of UTC: local June 1 starts at 2026-05-31 15:00 UTC.

    A row at 2026-05-31 16:00 UTC ≡ 2026-06-01 01:00 JST. UTC's May; JST's June.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from findajob.cost_rollups import spend_this_month

    _insert_job_with_costs(db, "job-tk-boundary", [])
    db.execute(
        """INSERT INTO cost_log (job_id, operation, model, cost_usd, success, logged_at)
           VALUES ('job-tk-boundary', 'briefing', 'g/m', 4.40, 1, '2026-05-31 16:00:00')"""
    )
    db.commit()

    now_jst = datetime(2026, 6, 5, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    now_utc = datetime(2026, 6, 5, 1, 0, tzinfo=ZoneInfo("UTC"))

    # JST view of June 2026: the boundary row counts.
    assert spend_this_month(db, tz="Asia/Tokyo", now=now_jst) == pytest.approx(4.40, rel=1e-3)
    # UTC view of June 2026: the row is in May, excluded.
    assert spend_this_month(db, tz="UTC", now=now_utc) == 0.0


def test_spend_this_month_default_tz_is_utc(db: sqlite3.Connection) -> None:
    """Default ``tz`` is ``"UTC"`` — preserves pre-#823 behavior for callers
    that don't pass a tz. Callers (spend_ceiling, web/app.py) read ``TZ`` env
    and pass it through, matching the ``weekly_spend`` pattern.
    """
    from findajob.cost_rollups import spend_this_month

    db.execute(
        """INSERT INTO cost_log (job_id, operation, model, cost_usd, success, logged_at)
           VALUES (NULL, 'briefing', 'g/m', 2.50, 1, datetime('now'))"""
    )
    db.commit()
    assert spend_this_month(db) == pytest.approx(2.50, rel=1e-3)
