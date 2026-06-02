"""findajob.timeutil — bucket naïve-UTC DB timestamps onto local (operator-TZ) days.

The pipeline stores audit_log / feedback_log timestamps as naïve UTC
("YYYY-MM-DD HH:MM:SS"). The operator's calendar runs in PT, so a 22:00 PT
transition — stored as the *next* UTC day — must bucket onto the PT day, not the
UTC day (#967). These tests pin that with explicit tz/now seams, so they carry no
process-global TZ state and stay deterministic on any host.
"""

from datetime import UTC, date, datetime

from findajob.timeutil import day_window_start_utc, today_local, utc_str_to_local_date

PT = "America/Los_Angeles"


def test_utc_string_buckets_onto_pt_day_not_utc_day() -> None:
    # 22:00 PDT Jun 1 == 05:00 UTC Jun 2 (PDT = UTC-7). A naïve date() on the
    # stored string buckets this as Jun 2; PT-correct bucketing is Jun 1.
    assert utc_str_to_local_date("2026-06-02 05:00:00", PT) == date(2026, 6, 1)


def test_utc_string_midday_agrees_with_utc() -> None:
    # 19:00 UTC Jun 1 == 12:00 PDT Jun 1 — unambiguous, both calendars agree.
    assert utc_str_to_local_date("2026-06-01 19:00:00", PT) == date(2026, 6, 1)


def test_today_local_resolves_in_tz_not_utc() -> None:
    # now = 05:00 UTC Jun 2 == 22:00 PDT Jun 1 — local "today" is Jun 1.
    now = datetime(2026, 6, 2, 5, 0, tzinfo=UTC)
    assert today_local(PT, now=now) == date(2026, 6, 1)


def test_day_window_start_is_pt_midnight_expressed_in_utc() -> None:
    # now = 22:00 PDT Jun 1. A 1-day window starts at PT-midnight Jun 1, which
    # is 07:00 UTC Jun 1 (PDT = UTC-7).
    now = datetime(2026, 6, 2, 5, 0, tzinfo=UTC)
    assert day_window_start_utc(1, PT, now=now) == "2026-06-01 07:00:00"


def test_day_window_start_spans_requested_days() -> None:
    # PT today = Jun 1; a 30-day window starts 29 days earlier = May 3.
    # PT-midnight May 3 (still PDT) = 07:00 UTC May 3.
    now = datetime(2026, 6, 2, 5, 0, tzinfo=UTC)
    assert day_window_start_utc(30, PT, now=now) == "2026-05-03 07:00:00"
