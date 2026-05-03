"""Tests for JobsApi14IndeedAdapter — restored Indeed coverage (#414)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.jobs_api14_indeed import JobsApi14IndeedAdapter


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RAPIDAPI_KEY", "JOBS_API14_KEY", "JSEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_class_attributes() -> None:
    adapter = JobsApi14IndeedAdapter()
    assert adapter.name == "jobs-api14-indeed"
    assert adapter.display_name == "Jobs API — Indeed (jobs-api14)"
    assert adapter.source_label == "jobsapi_indeed"  # matches retired-but-not-purged DB rows
    assert adapter.required_env_vars == ("RAPIDAPI_KEY", "JOBS_API14_KEY")


def test_is_configured_with_canonical_rapidapi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    assert JobsApi14IndeedAdapter().is_configured() is True


def test_is_configured_with_dedicated_jobs_api14_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    assert JobsApi14IndeedAdapter().is_configured() is True


def test_is_configured_false_when_unset() -> None:
    assert JobsApi14IndeedAdapter().is_configured() is False


def test_fetch_returns_empty_when_unconfigured() -> None:
    assert JobsApi14IndeedAdapter().fetch(["data center engineer"]) == []


def test_fetch_hits_indeed_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"hasError": False, "data": []}
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake_response) as mock_get:
        JobsApi14IndeedAdapter().fetch(["data center engineer"])

    args, kwargs = mock_get.call_args
    assert args[0] == "https://jobs-api14.p.rapidapi.com/v2/indeed/search"
    assert kwargs["headers"]["x-rapidapi-host"] == "jobs-api14.p.rapidapi.com"
    assert kwargs["headers"]["x-rapidapi-key"] == "test-key"
    assert kwargs["params"]["query"] == "data center engineer"
    assert kwargs["params"]["sortType"] == "date"
    assert kwargs["params"]["countryCode"] == "us"
    assert kwargs["params"]["location"] == "United States"


def test_fetch_parses_indeed_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "abc123",
                "title": "Data Center Engineer",
                "company": {"name": "Acme Corp", "addresses": [], "image": ""},
                "location": {"country": "United States", "countryCode": "US", "location": "Reston, VA"},
                "applyUrl": "https://example.com/apply/abc123",
                "description": "Job description body...",
            },
        ],
        "meta": {"count": 1, "nextToken": "tok456", "position": 0},
    }

    with patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake_response):
        rows = JobsApi14IndeedAdapter().fetch(["data center"])

    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Data Center Engineer"
    assert row["company"] == "Acme Corp"
    assert row["location"] == "Reston, VA"
    assert row["url"] == "https://example.com/apply/abc123"
    assert row["api_id"] == "abc123"
    assert row["source"] == "jobsapi_indeed"
    assert row["query"] == "data center"
    assert row["description"] == "Job description body..."  # inline JD


def test_fetch_drops_rows_with_no_title_or_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "x",
                "title": "",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u",
                "description": "d",
            },
            {
                "id": "y",
                "title": "Engineer",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "",
                "description": "d",
            },
            {
                "id": "z",
                "title": "Engineer",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u",
                "description": "d",
            },
        ],
        "meta": {"count": 3},
    }

    with patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake_response):
        rows = JobsApi14IndeedAdapter().fetch(["q"])

    # Only the third row should survive (has both title and url)
    assert len(rows) == 1
    assert rows[0]["api_id"] == "z"


def test_fetch_post_filter_drops_titles_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "1",
                "title": "Cashier",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u1",
                "description": "d",
            },
            {
                "id": "2",
                "title": "Senior Data Center Engineer",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u2",
                "description": "d",
            },
            {
                "id": "3",
                "title": "Operations Manager",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u3",
                "description": "d",
            },
            {
                "id": "4",
                "title": "Bartender",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u4",
                "description": "d",
            },
        ],
        "meta": {"count": 4},
    }

    with patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake_response):
        rows = JobsApi14IndeedAdapter().fetch(["q"])

    titles = [r["title"] for r in rows]
    assert "Senior Data Center Engineer" in titles
    assert "Operations Manager" in titles
    assert "Cashier" not in titles
    assert "Bartender" not in titles


def test_fetch_handles_429_with_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    rate_limited.json.return_value = {"hasError": False, "data": []}
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"hasError": False, "data": []}
    success.raise_for_status.return_value = None

    _mod = "findajob.fetchers.adapters.jobs_api14_indeed"
    with (
        patch(f"{_mod}.requests.get", side_effect=[rate_limited, success]) as mock_get,
        patch(f"{_mod}.time.sleep") as mock_sleep,
    ):
        JobsApi14IndeedAdapter().fetch(["engineer"])

    assert mock_get.call_count == 2
    assert mock_sleep.called


def test_fetch_handles_haserror_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"hasError": True, "errors": [{"message": "boom"}], "data": []}

    with patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake_response):
        rows = JobsApi14IndeedAdapter().fetch(["engineer"])

    assert rows == []


def test_live_test_auth_failure_with_no_key() -> None:
    result = JobsApi14IndeedAdapter().live_test(["engineer"])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_success_returns_per_query_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "1",
                "title": "Engineer",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u",
                "description": "d",
            },
        ],
    }

    with patch("findajob.fetchers.adapters.jobs_api14_indeed.requests.get", return_value=fake_response):
        result = JobsApi14IndeedAdapter().live_test(["engineer"])

    assert result.ok is True
    assert result.bucket == "success"
    assert len(result.per_query) == 1
    assert result.per_query[0].count == 1
