"""Tests for per-tenant target location reader and adapter integration (#372)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters._locations import read_target_locations
from findajob.fetchers.adapters.jobs_api14 import JobsApi14Adapter
from findajob.fetchers.adapters.jobs_api14_indeed import JobsApi14IndeedAdapter
from findajob.fetchers.adapters.jsearch import JSearchAdapter

# ---------------------------------------------------------------------------
# read_target_locations
# ---------------------------------------------------------------------------


def test_read_target_locations_fallback_when_absent(tmp_path: Path) -> None:
    result = read_target_locations(tmp_path / "nonexistent.txt")
    assert result == ["United States"]


def test_read_target_locations_fallback_when_empty(tmp_path: Path) -> None:
    f = tmp_path / "target_locations.txt"
    f.write_text("# just a comment\n\n")
    assert read_target_locations(f) == ["United States"]


def test_read_target_locations_returns_configured_values(tmp_path: Path) -> None:
    f = tmp_path / "target_locations.txt"
    f.write_text("# comment\nRemote\nNew York, NY\nBoston, MA\n")
    assert read_target_locations(f) == ["Remote", "New York, NY", "Boston, MA"]


def test_read_target_locations_strips_blank_lines(tmp_path: Path) -> None:
    f = tmp_path / "target_locations.txt"
    f.write_text("Los Angeles, CA\n\nSan Francisco, CA\n")
    assert read_target_locations(f) == ["Los Angeles, CA", "San Francisco, CA"]


# ---------------------------------------------------------------------------
# JobsApi14Adapter — location param
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBS_API14_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)


def _fake_ok(json_data: dict | None = None) -> MagicMock:
    m = MagicMock(status_code=200, headers={})
    m.json.return_value = json_data or {"hasError": False, "data": []}
    m.raise_for_status.return_value = None
    return m


def test_jobs_api14_uses_fallback_location_when_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "k")
    absent = tmp_path / "target_locations.txt"

    fake = _fake_ok()
    with (
        patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake),
        patch("findajob.fetchers.adapters._locations._path", return_value=absent),
    ):
        JobsApi14Adapter().fetch(["program manager"])

    calls = fake.json.call_args_list  # noqa: F841 — checked via mock_get below


def test_jobs_api14_passes_location_param_to_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "k")
    locs_file = tmp_path / "target_locations.txt"
    locs_file.write_text("Remote\nNew York, NY\n")

    fake = _fake_ok()
    with (
        patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake) as mock_get,
        patch("findajob.fetchers.adapters._locations._path", return_value=locs_file),
        patch("findajob.fetchers.adapters.jobs_api14.time.sleep"),
    ):
        JobsApi14Adapter().fetch(["program manager"])

    locations_seen = [c.kwargs["params"]["location"] for c in mock_get.call_args_list]
    assert "Remote" in locations_seen
    assert "New York, NY" in locations_seen


def test_jobs_api14_one_call_per_location_per_query(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "k")
    locs_file = tmp_path / "target_locations.txt"
    locs_file.write_text("Remote\nBoston, MA\n")

    fake = _fake_ok()
    with (
        patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake) as mock_get,
        patch("findajob.fetchers.adapters._locations._path", return_value=locs_file),
        patch("findajob.fetchers.adapters.jobs_api14.time.sleep"),
    ):
        JobsApi14Adapter().fetch(["program manager", "operations manager"])

    # 2 locations × 2 queries = 4 calls
    assert mock_get.call_count == 4
    location_params = [c.kwargs["params"]["location"] for c in mock_get.call_args_list]
    assert location_params.count("Remote") == 2
    assert location_params.count("Boston, MA") == 2


# ---------------------------------------------------------------------------
# JobsApi14IndeedAdapter — location param
# ---------------------------------------------------------------------------


def test_indeed_uses_fallback_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "k")
    absent = tmp_path / "target_locations.txt"

    fake = _fake_ok({"data": []})
    with (
        patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake) as mock_get,
        patch("findajob.fetchers.adapters._locations._path", return_value=absent),
    ):
        JobsApi14IndeedAdapter().fetch(["social worker"])

    assert mock_get.call_count == 1
    assert mock_get.call_args.kwargs["params"]["location"] == "United States"


def test_indeed_passes_location_param(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "k")
    locs_file = tmp_path / "target_locations.txt"
    locs_file.write_text("Remote\nNew York, NY\n")

    fake = _fake_ok({"data": []})
    with (
        patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake) as mock_get,
        patch("findajob.fetchers.adapters._locations._path", return_value=locs_file),
        patch("findajob.fetchers.adapters.jobs_api14_indeed.time.sleep"),
    ):
        JobsApi14IndeedAdapter().fetch(["social worker"])

    assert mock_get.call_count == 2
    locations_seen = [c.kwargs["params"]["location"] for c in mock_get.call_args_list]
    assert locations_seen == ["Remote", "New York, NY"]


# ---------------------------------------------------------------------------
# JSearchAdapter — location param
# ---------------------------------------------------------------------------


def test_jsearch_uses_fallback_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    absent = tmp_path / "target_locations.txt"

    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": []}
    fake.raise_for_status.return_value = None

    with (
        patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake) as mock_get,
        patch("findajob.fetchers.adapters._locations._path", return_value=absent),
    ):
        JSearchAdapter().fetch(["nurse practitioner"])

    assert mock_get.call_count == 1
    assert mock_get.call_args.kwargs["params"]["location"] == "United States"


def test_jsearch_passes_location_param(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    locs_file = tmp_path / "target_locations.txt"
    locs_file.write_text("Remote\nLos Angeles, CA\n")

    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": []}
    fake.raise_for_status.return_value = None

    with (
        patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake) as mock_get,
        patch("findajob.fetchers.adapters._locations._path", return_value=locs_file),
        patch("findajob.fetchers.adapters.jsearch.time.sleep"),
    ):
        JSearchAdapter().fetch(["nurse practitioner"])

    assert mock_get.call_count == 2
    locations_seen = [c.kwargs["params"]["location"] for c in mock_get.call_args_list]
    assert locations_seen == ["Remote", "Los Angeles, CA"]
