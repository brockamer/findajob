"""Conditional-formatting helpers for board rows."""

from datetime import UTC, datetime, timedelta

import pytest

from findajob.web.helpers import applied_age_bucket, remote_cell_class, stage_row_class


def _iso_days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat()


@pytest.mark.parametrize(
    "days,expected",
    [
        (0, "row-applied-fresh"),
        (6, "row-applied-fresh"),
        (7, "row-applied-week"),
        (13, "row-applied-week"),
        (14, "row-applied-stale"),
        (20, "row-applied-stale"),
        (21, "row-applied-cold"),
        (90, "row-applied-cold"),
    ],
)
def test_applied_age_bucket(days: int, expected: str) -> None:
    assert applied_age_bucket(_iso_days_ago(days)) == expected


def test_applied_age_bucket_none_returns_empty() -> None:
    assert applied_age_bucket(None) == ""


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("offer", "row-offer"),
        ("interview", "row-interviewing"),
        ("applied", ""),
        ("scored", ""),
    ],
)
def test_stage_row_class(stage: str, expected: str) -> None:
    assert stage_row_class(stage) == expected


@pytest.mark.parametrize(
    "remote,expected_contains",
    [
        ("Remote", "text-green"),
        ("Hybrid", "text-amber"),
        ("On-site", "text-slate"),
        ("", ""),
        (None, ""),
    ],
)
def test_remote_cell_class(remote: str | None, expected_contains: str) -> None:
    result = remote_cell_class(remote)
    if expected_contains:
        assert expected_contains in result
    else:
        assert result == ""
