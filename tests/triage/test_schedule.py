"""Tests for findajob.triage.schedule.next_triage_time (#752)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from findajob.triage.schedule import _parse_cron, _parse_field, next_triage_time


def test_parse_field_star() -> None:
    assert _parse_field("*", 0, 59) == set(range(60))
    assert _parse_field("*", 0, 6) == set(range(7))


def test_parse_field_single_int() -> None:
    assert _parse_field("0", 0, 59) == {0}
    assert _parse_field("30", 0, 59) == {30}


def test_parse_field_out_of_range() -> None:
    assert _parse_field("60", 0, 59) is None
    assert _parse_field("-1", 0, 59) is None


def test_parse_field_unsupported() -> None:
    assert _parse_field("*/10", 0, 59) is None
    assert _parse_field("0,30", 0, 59) is None
    assert _parse_field("1-5", 0, 23) is None
    assert _parse_field("abc", 0, 59) is None


def test_parse_cron_daily_midnight() -> None:
    parsed = _parse_cron("0 0 * * *")
    assert parsed is not None
    minutes, hours, mdays, months, dows = parsed
    assert minutes == {0}
    assert hours == {0}
    assert mdays == set(range(1, 32))
    assert months == set(range(1, 13))
    assert dows == set(range(7))


def test_parse_cron_wrong_field_count() -> None:
    assert _parse_cron("0 0 * *") is None
    assert _parse_cron("0 0 * * * *") is None


def test_parse_cron_unsupported_form() -> None:
    assert _parse_cron("*/10 * * * *") is None
    assert _parse_cron("0 6-18 * * *") is None


@patch.dict("os.environ", {"FINDAJOB_TRIAGE_SCHEDULE": "0 0 * * *", "FINDAJOB_TRIAGE_ENABLED": "true"})
def test_next_triage_time_daily_midnight_from_morning() -> None:
    now = datetime(2026, 5, 20, 9, 30, 17)
    result = next_triage_time(now=now)
    assert result == datetime(2026, 5, 21, 0, 0)


@patch.dict("os.environ", {"FINDAJOB_TRIAGE_SCHEDULE": "0 0 * * *", "FINDAJOB_TRIAGE_ENABLED": "true"})
def test_next_triage_time_advances_past_current_minute() -> None:
    # When called exactly at the fire minute, returns the NEXT fire (not current).
    now = datetime(2026, 5, 20, 0, 0, 0)
    result = next_triage_time(now=now)
    assert result == datetime(2026, 5, 21, 0, 0)


@patch.dict("os.environ", {"FINDAJOB_TRIAGE_SCHEDULE": "5 0 * * *", "FINDAJOB_TRIAGE_ENABLED": "true"})
def test_next_triage_time_stagger_offset() -> None:
    # Realistic stagger plan schedule: 5 minutes past midnight.
    now = datetime(2026, 5, 20, 23, 50, 0)
    result = next_triage_time(now=now)
    assert result == datetime(2026, 5, 21, 0, 5)


@patch.dict("os.environ", {"FINDAJOB_TRIAGE_SCHEDULE": "0 0 * * *", "FINDAJOB_TRIAGE_ENABLED": "false"})
def test_next_triage_time_disabled_returns_none() -> None:
    now = datetime(2026, 5, 20, 9, 30)
    assert next_triage_time(now=now) is None


@patch.dict("os.environ", {"FINDAJOB_TRIAGE_SCHEDULE": "*/10 * * * *", "FINDAJOB_TRIAGE_ENABLED": "true"})
def test_next_triage_time_unparseable_returns_none() -> None:
    now = datetime(2026, 5, 20, 9, 30)
    assert next_triage_time(now=now) is None


@patch.dict("os.environ", {}, clear=True)
def test_next_triage_time_no_env_falls_back_to_yaml() -> None:
    # No env override; should read ops/scheduled-jobs.yaml's "0 0 * * *" triage entry.
    now = datetime(2026, 5, 20, 9, 30)
    result = next_triage_time(now=now)
    # Yaml default is "0 0 * * *" enabled=true; expect next midnight.
    assert result == datetime(2026, 5, 21, 0, 0)
