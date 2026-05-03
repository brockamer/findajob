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
    assert adapter.required_env_vars == ("RAPIDAPI_KEY", "JOBS_API14_KEY")


def test_is_configured_false_when_env_unset() -> None:
    assert JobsApi14Adapter().is_configured() is False


def test_is_configured_true_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key-1234")
    assert JobsApi14Adapter().is_configured() is True


def test_is_configured_falls_back_to_rapidapi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared RAPIDAPI_KEY backs JobsApi14Adapter when JOBS_API14_KEY is unset (#414)."""
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    assert JobsApi14Adapter().is_configured() is True


def test_is_configured_canonical_wins_over_dedicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAPIDAPI_KEY is canonical; when both are set, canonical wins (#414)."""
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    # Both adapters use canonical-first lookup; canonical wins
    assert JobsApi14Adapter().is_configured() is True


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


# ── #414 PR2 — JOBS_API14_MAX_PAGES + nextToken pagination ──


def _page(rows: list[dict], next_token: str | None) -> MagicMock:
    """Build a fake jobs-api14 LinkedIn search response with optional nextToken."""
    response = MagicMock(status_code=200, headers={})
    response.json.return_value = {
        "hasError": False,
        "data": rows,
        "meta": {"nextToken": next_token} if next_token else {},
    }
    response.raise_for_status.return_value = None
    return response


def _row(idx: int) -> dict:
    return {
        "id": f"ext-{idx}",
        "title": f"Engineer {idx}",
        "company": "Acme",
        "location": "Remote",
        "linkedinUrl": f"https://example.com/{idx}",
    }


def test_max_pages_default_is_one() -> None:
    assert JobsApi14Adapter._max_pages() == 1


def test_max_pages_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "5")
    assert JobsApi14Adapter._max_pages() == 5


def test_max_pages_clamps_to_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "999")
    assert JobsApi14Adapter._max_pages() == 20


def test_max_pages_clamps_to_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "0")
    assert JobsApi14Adapter._max_pages() == 1


def test_max_pages_invalid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "abc")
    assert JobsApi14Adapter._max_pages() == 1


def test_fetch_single_page_when_max_pages_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default max_pages=1 → exactly one HTTP call per query, even if nextToken is present."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    response = _page([_row(1), _row(2)], next_token="should-be-ignored")
    with patch("findajob.fetchers.adapters.jobs_api14.requests.get", return_value=response) as mock_get:
        rows = JobsApi14Adapter().fetch(["engineer"])
    assert mock_get.call_count == 1
    assert len(rows) == 2


def test_fetch_loops_to_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """JOBS_API14_MAX_PAGES=3 + chained nextTokens → exactly 3 HTTP calls."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "3")
    p1 = _page([_row(i) for i in range(10)], next_token="t1")
    p2 = _page([_row(10 + i) for i in range(10)], next_token="t2")
    p3 = _page([_row(20 + i) for i in range(10)], next_token="t3")
    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        side_effect=[p1, p2, p3],
    ) as mock_get:
        rows = JobsApi14Adapter().fetch(["engineer"])
    assert mock_get.call_count == 3
    assert len(rows) == 30


def test_fetch_stops_when_next_token_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loop breaks when API returns no nextToken, even if max_pages allows more."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "5")
    p1 = _page([_row(1)], next_token="t1")
    p2 = _page([_row(2)], next_token=None)  # no token → stop
    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        side_effect=[p1, p2],
    ) as mock_get:
        rows = JobsApi14Adapter().fetch(["engineer"])
    assert mock_get.call_count == 2
    assert len(rows) == 2


def test_fetch_uses_token_only_on_pagination_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pagination calls send only {"token": ...}; original query/location params are dropped."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "2")
    p1 = _page([_row(1)], next_token="page-2-token")
    p2 = _page([_row(2)], next_token=None)
    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        side_effect=[p1, p2],
    ) as mock_get:
        JobsApi14Adapter().fetch(["data center engineer"])
    first_params = mock_get.call_args_list[0].kwargs["params"]
    second_params = mock_get.call_args_list[1].kwargs["params"]
    assert first_params["query"] == "data center engineer"
    assert second_params == {"token": "page-2-token"}
    assert "query" not in second_params


def test_fetch_logs_pages_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """jobsapi_fetched event includes pages= for observability."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "3")
    p1 = _page([_row(1)], next_token="t1")
    p2 = _page([_row(2)], next_token=None)
    with (
        patch("findajob.fetchers.adapters.jobs_api14.requests.get", side_effect=[p1, p2]),
        patch("findajob.fetchers.adapters.jobs_api14.log_event") as mock_log,
    ):
        JobsApi14Adapter().fetch(["engineer"])
    fetched_events = [c for c in mock_log.call_args_list if c.args[0] == "jobsapi_fetched"]
    assert len(fetched_events) == 1
    kwargs = fetched_events[0].kwargs
    assert kwargs["pages"] == 2
    assert kwargs["count"] == 2


def test_live_test_does_not_paginate(monkeypatch: pytest.MonkeyPatch) -> None:
    """live_test stays single-page even when JOBS_API14_MAX_PAGES is raised.

    Onboarding-time spot check is connectivity-only.
    """
    monkeypatch.setenv("JOBS_API14_KEY", "good-key")
    monkeypatch.setenv("JOBS_API14_MAX_PAGES", "5")
    response = _page([_row(1)], next_token="should-be-ignored-by-live-test")
    with patch(
        "findajob.fetchers.adapters.jobs_api14.requests.get",
        return_value=response,
    ) as mock_get:
        result = JobsApi14Adapter().live_test(["q1", "q2"])
    # 2 queries × 1 page each = 2 calls (NOT 2 × 5 = 10)
    assert mock_get.call_count == 2
    assert result.ok is True
