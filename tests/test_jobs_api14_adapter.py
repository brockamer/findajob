"""Tests for JobsApi14Adapter (#408 refactor of fetch_jobsapi_jobs)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.jobs_api14 import JobsApi14Adapter


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBS_API14_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)


def test_class_attributes() -> None:
    adapter = JobsApi14Adapter()
    assert adapter.name == "jobs-api14"
    assert adapter.display_name == "Jobs API (jobs-api14)"
    assert adapter.source_label == "jobsapi_linkedin"  # preserves existing DB rows
    assert adapter.required_env_vars == ("JOBS_API14_KEY",)


def test_is_configured_false_when_env_unset() -> None:
    assert JobsApi14Adapter().is_configured() is False


def test_is_configured_true_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key-1234")
    assert JobsApi14Adapter().is_configured() is True


def test_is_configured_does_not_fall_back_to_rapidapi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No production-code fallback. Migration handles RAPIDAPI_KEY at entrypoint."""
    monkeypatch.setenv("RAPIDAPI_KEY", "old-key-1234")
    assert JobsApi14Adapter().is_configured() is False


def test_fetch_returns_empty_when_unconfigured() -> None:
    adapter = JobsApi14Adapter()
    assert adapter.fetch(["engineer"]) == []


def test_fetch_hits_correct_endpoint_with_correct_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"hasError": False, "data": []}
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake_response) as mock_get:
        JobsApi14Adapter().fetch(["data center engineer"])

    args, kwargs = mock_get.call_args
    assert args[0] == "https://jobs-api14.p.rapidapi.com/v2/linkedin/search"
    assert kwargs["headers"]["x-rapidapi-host"] == "jobs-api14.p.rapidapi.com"
    assert kwargs["headers"]["x-rapidapi-key"] == "test-key"
    assert kwargs["params"]["query"] == "data center engineer"


def test_fetch_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    rate_limited.json.return_value = {"hasError": False, "data": []}
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"hasError": False, "data": []}
    success.raise_for_status.return_value = None

    with (
        patch("findajob.fetchers.adapters.jobs_api14.requests.get", side_effect=[rate_limited, success]) as mock_get,
        patch("findajob.fetchers.adapters.jobs_api14.time.sleep") as mock_sleep,
    ):
        JobsApi14Adapter().fetch(["engineer"])

    assert mock_get.call_count == 2
    assert mock_sleep.called


def test_fetch_returns_parsed_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock(status_code=200, headers={})
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "ext-1",
                "title": "Senior Data Center Engineer",
                "company": "Acme Corp",
                "location": "Seattle, WA",
                "linkedinUrl": "https://www.linkedin.com/jobs/view/123",
            },
        ],
    }
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake_response):
        rows = JobsApi14Adapter().fetch(["engineer"])

    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Data Center Engineer"
    assert rows[0]["company"] == "Acme Corp"
    assert "linkedin.com/jobs/view/123" in rows[0]["url"]


def test_live_test_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "bad-key")
    fake_response = MagicMock(status_code=401, headers={})
    fake_response.raise_for_status.side_effect = Exception("401")

    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake_response):
        result = JobsApi14Adapter().live_test(["engineer"])

    assert result.ok is False
    assert result.bucket == "auth"
    assert result.auth_error is not None


def test_live_test_zero_rows_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    fake_response = MagicMock(status_code=200, headers={})
    fake_response.json.return_value = {"hasError": False, "data": []}
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake_response):
        result = JobsApi14Adapter().live_test(["engineer", "manager"])

    assert result.ok is True
    assert result.bucket == "zero_rows"
    assert all(qr.count == 0 for qr in result.per_query)


def test_live_test_mixed_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    one_row = MagicMock(status_code=200, headers={})
    one_row.json.return_value = {"hasError": False, "data": [{"title": "X", "company": "Y"}]}
    one_row.raise_for_status.return_value = None
    no_rows = MagicMock(status_code=200, headers={})
    no_rows.json.return_value = {"hasError": False, "data": []}
    no_rows.raise_for_status.return_value = None

    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        side_effect=[one_row, no_rows],
    ):
        result = JobsApi14Adapter().live_test(["engineer", "manager"])

    assert result.ok is True
    assert result.bucket == "mixed"
    assert result.per_query[0].count == 1
    assert result.per_query[1].count == 0


def test_live_test_rate_limit_mid_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"hasError": False, "data": [{"title": "X", "company": "Y"}]}
    success.raise_for_status.return_value = None
    rate_limit = MagicMock(status_code=429, headers={"Retry-After": "60"})
    rate_limit.raise_for_status.side_effect = Exception("429")

    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        side_effect=[success, rate_limit],
    ):
        result = JobsApi14Adapter().live_test(["engineer", "manager"])

    assert result.ok is True
    assert result.bucket == "rate_limit"
    assert result.per_query[0].count == 1


def test_fetch_calls_clean_title_and_clean_company(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row parsing must apply clean_title() and clean_company() like the legacy fetcher."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock(status_code=200, headers={})
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "ext-1",
                "title": "Senior Engineer · 5 days ago · 100 applicants",  # raw with appended metadata
                "company": {"name": "Acme Corp"},  # nested dict shape
                "location": "Seattle, WA",
                "linkedinUrl": "https://www.linkedin.com/jobs/view/123",
            },
        ],
    }
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake_response):
        rows = JobsApi14Adapter().fetch(["engineer"])

    # clean_title strips trailing metadata; nested company name is unwrapped
    assert "·" not in rows[0]["title"]
    assert "5 days ago" not in rows[0]["title"]
    assert rows[0]["company"] == "Acme Corp"


def test_fetch_drops_rows_with_empty_title_or_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy parity — rows missing title or url are skipped."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock(status_code=200, headers={})
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {"id": "1", "title": "", "company": "Acme", "linkedinUrl": "https://example.com/1"},  # empty title — drop
            {"id": "2", "title": "Engineer", "company": "Acme", "linkedinUrl": ""},  # empty url — drop
            {"id": "3", "title": "Engineer", "company": "Acme", "linkedinUrl": "https://example.com/3"},  # keep
        ],
    }
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake_response):
        rows = JobsApi14Adapter().fetch(["engineer"])

    assert len(rows) == 1
    assert rows[0]["api_id"] == "3"


def test_fetch_handles_nested_dict_location(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some LinkedIn responses return location as {location: 'X'} dict — unwrap it."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock(status_code=200, headers={})
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "ext-1",
                "title": "Engineer",
                "company": "Acme",
                "location": {"location": "Seattle, WA"},  # nested dict
                "linkedinUrl": "https://example.com/1",
            },
        ],
    }
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake_response):
        rows = JobsApi14Adapter().fetch(["engineer"])

    assert rows[0]["location"] == "Seattle, WA"


def test_fetch_paces_between_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy pacing — 0.6s sleep between queries."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"hasError": False, "data": []}
    fake.raise_for_status.return_value = None

    with (
        patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake),
        patch("findajob.fetchers.adapters.jobs_api14.time.sleep") as mock_sleep,
    ):
        JobsApi14Adapter().fetch(["query1", "query2", "query3"])

    # at least one sleep call with 0.6 (between queries)
    sleep_calls = [c.args for c in mock_sleep.call_args_list]
    assert (0.6,) in sleep_calls or any(c[0] == 0.6 for c in sleep_calls)


def test_live_test_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"hasError": False, "data": [{"title": "X", "company": "Y"}]}
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake):
        result = JobsApi14Adapter().live_test(["q1", "q2"])
    assert result.ok is True
    assert result.bucket == "success"
    assert all(qr.count == 1 for qr in result.per_query)


def test_live_test_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    fake = MagicMock(status_code=503, headers={})
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=fake):
        result = JobsApi14Adapter().live_test(["q1"])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_error_on_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests as req

    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        side_effect=req.ConnectionError("DNS failure"),
    ):
        result = JobsApi14Adapter().live_test(["q1", "q2"])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_network_error_mid_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network error after first query succeeds → partial-result, bucket=rate_limit."""
    import requests as req

    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"hasError": False, "data": [{"title": "X"}]}
    success.raise_for_status.return_value = None
    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        side_effect=[success, req.ConnectionError("DNS failure")],
    ):
        result = JobsApi14Adapter().live_test(["q1", "q2"])
    assert result.ok is True
    assert result.bucket == "rate_limit"
    assert len(result.per_query) == 1
