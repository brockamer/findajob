"""Tests for `_date_posted_for_install()` widening behavior.

Moved from `findajob.fetchers._date_posted_for_install` to
`findajob.fetchers.adapters.jobs_api14._date_posted_for_install` in #410.5
when the dead `fetch_jobsapi_jobs` wrapper (and its orphaned helper) were
deleted from `findajob/fetchers/__init__.py`. The function does a lazy
`from findajob.paths import BASE` per-call (matches the pattern in the
other adapter modules), so the test patches `findajob.paths.BASE` rather
than the adapter module's namespace.
"""

import time
from pathlib import Path

import pytest

from findajob import paths
from findajob.fetchers.adapters import jobs_api14


@pytest.fixture
def fake_base(monkeypatch, tmp_path):
    """Point findajob.paths.BASE at a tmpdir so we can plant a sentinel file."""
    monkeypatch.setattr(paths, "BASE", str(tmp_path))
    (tmp_path / "data").mkdir()
    return tmp_path


def _set_sentinel_age(base: Path, age_days: float) -> None:
    sentinel = base / "data" / ".onboarding-complete"
    sentinel.write_text("done")
    mtime = time.time() - (age_days * 86400)
    import os

    os.utime(sentinel, (mtime, mtime))


def test_no_sentinel_returns_day(fake_base):
    """Pre-onboarding stack falls back to current behavior."""
    assert jobs_api14._date_posted_for_install() == "day"


def test_fresh_install_returns_month(fake_base):
    """Sentinel under 30 days old → widened to month."""
    _set_sentinel_age(fake_base, age_days=1)
    assert jobs_api14._date_posted_for_install() == "month"


def test_just_under_threshold_returns_month(fake_base):
    """Day 29 still widened."""
    _set_sentinel_age(fake_base, age_days=29.5)
    assert jobs_api14._date_posted_for_install() == "month"


def test_at_or_past_threshold_returns_day(fake_base):
    """Day 30+ returns to default `day`."""
    _set_sentinel_age(fake_base, age_days=30.5)
    assert jobs_api14._date_posted_for_install() == "day"
    _set_sentinel_age(fake_base, age_days=365)
    assert jobs_api14._date_posted_for_install() == "day"
