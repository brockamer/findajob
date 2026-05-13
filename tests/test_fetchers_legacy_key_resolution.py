"""Tests that legacy fetcher paths in fetchers/__init__.py route through the
shared RapidAPI key resolver (#414)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers import fetch_linkedin_job_data


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RAPIDAPI_KEY", "JOBS_API14_KEY", "JSEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _make_fake_resp(description: str = "D", company_name: str = "C", title: str = "T") -> MagicMock:
    """Build a minimal fake requests.Response for the LinkedIn get endpoint."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"data": {"description": description, "companyName": company_name, "title": title}}
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_linkedin_job_data_returns_none_when_no_key() -> None:
    """No key set under any name → returns sentinel without HTTP call."""
    result = fetch_linkedin_job_data("12345")
    assert result == {"description": None, "company": None, "title": None}


def test_fetch_linkedin_job_data_uses_canonical_rapidapi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Canonical RAPIDAPI_KEY is honored (#414)."""
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    # fetch_linkedin_job_data does `import requests as req` inside the function;
    # patching `requests.get` at the module level intercepts req.get calls.
    with patch("requests.get", return_value=_make_fake_resp()) as mock_get, patch("findajob.fetchers.time.sleep"):
        result = fetch_linkedin_job_data("12345")
    assert result == {"description": "D", "company": "C", "title": "T"}
    assert mock_get.call_args.kwargs["headers"]["x-rapidapi-key"] == "shared-1234"


def test_fetch_linkedin_job_data_falls_back_to_jobs_api14_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy JOBS_API14_KEY-only stacks continue to work (#414 fallback)."""
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    with patch("requests.get", return_value=_make_fake_resp()) as mock_get, patch("findajob.fetchers.time.sleep"):
        result = fetch_linkedin_job_data("12345")
    assert result == {"description": "D", "company": "C", "title": "T"}
    assert mock_get.call_args.kwargs["headers"]["x-rapidapi-key"] == "legacy-1234"


def test_fetch_linkedin_job_data_canonical_wins_over_dedicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """If both are set, canonical wins (matches adapter behavior)."""
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    with patch("requests.get", return_value=_make_fake_resp()) as mock_get, patch("findajob.fetchers.time.sleep"):
        fetch_linkedin_job_data("12345")
    assert mock_get.call_args.kwargs["headers"]["x-rapidapi-key"] == "shared-1234"
