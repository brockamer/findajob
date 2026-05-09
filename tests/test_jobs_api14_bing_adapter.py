"""Tests for JobsApi14BingAdapter — Bing endpoint via jobs-api14 (#422).

Sibling of `JobsApi14IndeedAdapter`. Differences from Indeed:
- Endpoint `/v2/bing/search` (vs `/v2/indeed/search`)
- source_label `jobsapi_bing`
- name `jobs-api14-bing`
- NO title allowlist initially — AC #4 calls for an empirical decision
  after one triage-day measurement; deferred to a follow-up issue. So
  the post-filter test from `test_jobs_api14_indeed_adapter.py` does
  NOT have an analog here.

Shares JOBS_API14_KEY / RAPIDAPI_KEY with the LinkedIn + Indeed
adapters via the resolver (#414).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.jobs_api14_bing import JobsApi14BingAdapter


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RAPIDAPI_KEY", "JOBS_API14_KEY", "JSEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_class_attributes() -> None:
    adapter = JobsApi14BingAdapter()
    assert adapter.name == "jobs-api14-bing"
    assert adapter.display_name == "Jobs API — Bing (jobs-api14)"
    assert adapter.source_label == "jobsapi_bing"
    assert adapter.required_env_vars == ("RAPIDAPI_KEY", "JOBS_API14_KEY")


def test_is_configured_with_canonical_rapidapi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    assert JobsApi14BingAdapter().is_configured() is True


def test_is_configured_with_dedicated_jobs_api14_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per #414 the canonical key is RAPIDAPI_KEY but JOBS_API14_KEY remains
    a valid fallback for legacy stacks. Bing inherits the same resolver."""
    monkeypatch.setenv("JOBS_API14_KEY", "legacy-1234")
    assert JobsApi14BingAdapter().is_configured() is True


def test_is_configured_false_when_unset() -> None:
    assert JobsApi14BingAdapter().is_configured() is False


def test_fetch_returns_empty_when_unconfigured() -> None:
    assert JobsApi14BingAdapter().fetch(["data center engineer"]) == []


def test_fetch_hits_bing_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"hasError": False, "data": []}
    fake_response.raise_for_status.return_value = None

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response) as mock_get:
        JobsApi14BingAdapter().fetch(["data center engineer"])

    args, kwargs = mock_get.call_args
    assert args[0] == "https://jobs-api14.p.rapidapi.com/v2/bing/search"
    assert kwargs["headers"]["x-rapidapi-host"] == "jobs-api14.p.rapidapi.com"
    assert kwargs["headers"]["x-rapidapi-key"] == "test-key"
    assert kwargs["params"]["query"] == "data center engineer"
    # Defensive params (mirrors Indeed strategy — Bing's per-call filter
    # surface is unverified at PR time so we pin the conservative set).
    assert kwargs["params"]["countryCode"] == "us"
    assert kwargs["params"]["location"] == "United States"


def test_fetch_parses_bing_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Best-guess response shape: mirrors Indeed (inline description, applyUrl).
    Operator's first triage day will validate against the real API; if Bing
    uses different field names a fast-follow PR adjusts. Fixture documents
    the assumed shape."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {
        "hasError": False,
        "data": [
            {
                "id": "bing-001",
                "title": "Data Center Engineer",
                "company": {"name": "Acme Corp"},
                "location": {"country": "United States", "location": "Reston, VA"},
                "applyUrl": "https://example.com/bing-apply/001",
                "description": "Bing-sourced JD body...",
            },
        ],
        "meta": {"count": 1},
    }

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response):
        rows = JobsApi14BingAdapter().fetch(["data center"])

    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Data Center Engineer"
    assert row["company"] == "Acme Corp"
    assert row["location"] == "Reston, VA"
    assert row["url"] == "https://example.com/bing-apply/001"
    assert row["api_id"] == "bing-001"
    assert row["source"] == "jobsapi_bing"
    assert row["query"] == "data center"
    # Inline JD per AC #2 — no separate /v2/bing/get round-trip.
    assert row["description"] == "Bing-sourced JD body..."


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

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response):
        rows = JobsApi14BingAdapter().fetch(["q"])

    assert len(rows) == 1
    assert rows[0]["api_id"] == "z"


def test_fetch_passes_all_titles_through_when_no_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hot-zone behavior: AC #4 deferred the title-allowlist decision to a
    post-triage-day measurement. Until that follow-up lands, ALL titles
    flow through (no Indeed-style post-filter). This test locks in the
    allow-all default so a future "while we're here" allowlist add fails
    CI loudly rather than silently dropping rows from operator's stack
    on the next deploy."""
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
                "title": "Bartender",
                "company": {"name": "A"},
                "location": {"location": "X"},
                "applyUrl": "u3",
                "description": "d",
            },
        ],
        "meta": {"count": 3},
    }

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response):
        rows = JobsApi14BingAdapter().fetch(["q"])

    titles = {r["title"] for r in rows}
    assert titles == {"Cashier", "Senior Data Center Engineer", "Bartender"}, (
        "AC #4 deferral: Bing must not apply a title allowlist until the "
        "empirical post-triage-day measurement decides one. Allow-all is "
        "the contract; lock it in."
    )


def test_fetch_handles_429_with_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    rate_limited.json.return_value = {"hasError": False, "data": []}
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"hasError": False, "data": []}
    success.raise_for_status.return_value = None

    _mod = "findajob.fetchers.adapters.jobs_api14_bing"
    with (
        patch(f"{_mod}.requests.get", side_effect=[rate_limited, success]) as mock_get,
        patch(f"{_mod}.time.sleep") as mock_sleep,
    ):
        JobsApi14BingAdapter().fetch(["engineer"])

    assert mock_get.call_count == 2
    assert mock_sleep.called


def test_fetch_handles_haserror_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"hasError": True, "errors": [{"message": "boom"}], "data": []}

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response):
        rows = JobsApi14BingAdapter().fetch(["engineer"])

    assert rows == []


def test_live_test_auth_failure_with_no_key() -> None:
    result = JobsApi14BingAdapter().live_test(["engineer"])
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

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response):
        result = JobsApi14BingAdapter().live_test(["engineer"])

    assert result.ok is True
    assert result.bucket == "success"
    assert len(result.per_query) == 1
    assert result.per_query[0].count == 1
