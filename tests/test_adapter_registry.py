"""Tests for the adapter registry (#408)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from findajob.fetchers.adapters import iter_configured_adapters
from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS


def test_registry_contains_both_adapters() -> None:
    names = {cls.name for cls in REGISTERED_ADAPTERS}
    assert "jobs-api14" in names
    assert "jsearch" in names


def test_iter_configured_adapters_filters_by_active_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "k")
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    active = tmp_path / "active.txt"
    active.write_text("jobs-api14\n")
    with patch("findajob.fetchers.adapters.registry._active_sources_path", return_value=active):
        names = [a.name for a in iter_configured_adapters()]
    assert names == ["jobs-api14"]


def test_iter_configured_adapters_skips_unconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter listed in active_sources.txt but missing env var → skipped, logged."""
    monkeypatch.delenv("JOBS_API14_KEY", raising=False)
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    active = tmp_path / "active.txt"
    active.write_text("jobs-api14\njsearch\n")
    with patch("findajob.fetchers.adapters.registry._active_sources_path", return_value=active):
        names = [a.name for a in iter_configured_adapters()]
    assert names == ["jsearch"]


def test_iter_configured_adapters_skips_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter name in active_sources.txt that isn't registered is silently skipped."""
    monkeypatch.setenv("JOBS_API14_KEY", "k")
    active = tmp_path / "active.txt"
    active.write_text("jobs-api14\nworkday\n")  # workday not registered in this PR
    with patch("findajob.fetchers.adapters.registry._active_sources_path", return_value=active):
        names = [a.name for a in iter_configured_adapters()]
    assert names == ["jobs-api14"]


def test_iter_configured_adapters_default_when_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After #410.5 the default expanded to all 7 registered adapters. Only those
    that pass `is_configured()` get yielded — with just `JOBS_API14_KEY` set
    and no feed_urls.txt / gmail.json on disk, only the two adapters that
    accept that key are yielded (`jobs-api14` and `jobs-api14-indeed` per
    #414's RAPIDAPI_KEY consolidation; `jsearch` needs its own key).
    """
    monkeypatch.setenv("JOBS_API14_KEY", "k")
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    nonexistent = tmp_path / "missing.txt"
    with patch("findajob.fetchers.adapters.registry._active_sources_path", return_value=nonexistent):
        names = [a.name for a in iter_configured_adapters()]
    assert set(names) == {"jobs-api14", "jobs-api14-indeed"}


def test_jobs_api14_indeed_adapter_is_registered() -> None:
    """JobsApi14IndeedAdapter (#414) ships in the registry alongside its sibling."""
    from findajob.fetchers.adapters.jobs_api14_indeed import JobsApi14IndeedAdapter
    from findajob.fetchers.adapters.registry import REGISTERED_ADAPTERS

    assert JobsApi14IndeedAdapter in REGISTERED_ADAPTERS
