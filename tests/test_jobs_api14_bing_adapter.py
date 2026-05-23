"""Tests for JobsApi14BingAdapter — Bing endpoint via jobs-api14 (#422, #765).

Sibling of `JobsApi14IndeedAdapter`. Differences from Indeed:
- Endpoint pair `/v2/bing/search` + `/v2/bing/get` (vs Indeed's inline search)
- source_label `jobsapi_bing`
- name `jobs-api14-bing`
- NO title allowlist initially — AC #4 calls for an empirical decision
  after one triage-day measurement; deferred to a follow-up issue. So
  the post-filter test from `test_jobs_api14_indeed_adapter.py` does
  NOT have an analog here.

Two-call shape (#765): `/v2/bing/search` returns lightweight summary rows
with `id` + `title` + `location`. `applyUrl` + `description` + `companyName`
only come back from `/v2/bing/get?id=<base64_id>`. Fixtures below mirror
that real response shape, validated against the operator's stack in the
post-fix re-run of #601.

Shares JOBS_API14_KEY / RAPIDAPI_KEY with the LinkedIn + Indeed
adapters via the resolver (#414).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.jobs_api14_bing import JobsApi14BingAdapter


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RAPIDAPI_KEY", "JOBS_API14_KEY", "JSEARCH_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_sleep() -> Any:
    """Skip real sleeps so the test suite stays fast (0.6s × 18 get-calls
    would dominate the run time)."""
    with patch("findajob.fetchers.adapters.jobs_api14_bing.time.sleep"):
        yield


def _ok_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _two_call_side_effect(search_payload: dict, detail_by_id: dict[str, dict]) -> Any:
    """Route `requests.get` calls to /v2/bing/search or /v2/bing/get based
    on the URL argument. Detail responses keyed by `params['id']`."""

    def _side_effect(
        url: str,
        headers: dict | None = None,
        params: dict | None = None,
        timeout: int | None = None,
    ) -> MagicMock:
        if url.endswith("/v2/bing/search"):
            return _ok_response(search_payload)
        if url.endswith("/v2/bing/get"):
            assert params is not None and "id" in params, "get-call must pass id"
            payload = detail_by_id.get(params["id"], {"hasError": True, "errors": [{"message": "no fixture"}]})
            return _ok_response(payload)
        raise AssertionError(f"unexpected URL: {url}")

    return _side_effect


def test_class_attributes() -> None:
    adapter = JobsApi14BingAdapter()
    assert adapter.name == "jobs-api14-bing"
    assert adapter.display_name == "Jobs API — Bing (jobs-api14)"
    assert adapter.source_label == "jobsapi_bing"
    assert adapter.required_env_vars == ("RAPIDAPI_KEY", "JOBS_API14_KEY")


def test_two_endpoint_class_vars() -> None:
    # The two-call pattern is part of the adapter contract — assert both
    # endpoints are class-attached and distinct so a future "let's unify"
    # refactor doesn't silently lose the get-call.
    adapter = JobsApi14BingAdapter()
    assert adapter._SEARCH_ENDPOINT == "https://jobs-api14.p.rapidapi.com/v2/bing/search"
    assert adapter._GET_ENDPOINT == "https://jobs-api14.p.rapidapi.com/v2/bing/get"


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


def test_fetch_hits_search_endpoint_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {"hasError": False, "data": []}

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, {}),
    ) as mock_get:
        JobsApi14BingAdapter().fetch(["data center engineer"])

    # Empty search → no get-call follows. Only the search hit.
    assert mock_get.call_count == 1
    args, kwargs = mock_get.call_args
    assert args[0] == "https://jobs-api14.p.rapidapi.com/v2/bing/search"
    assert kwargs["headers"]["x-rapidapi-host"] == "jobs-api14.p.rapidapi.com"
    assert kwargs["headers"]["x-rapidapi-key"] == "test-key"
    assert kwargs["params"]["query"] == "data center engineer"
    assert kwargs["params"]["countryCode"] == "us"
    assert kwargs["params"]["location"] == "United States"


def test_fetch_stitches_search_and_get_into_full_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #1 + #2: fetch() calls /v2/bing/get?id=<id> per search row,
    populates applyUrl → url, description, and companyName from the get
    response. Search-only fields (title, location, id) come from search."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {
                "id": "bing-001",
                "title": "Data Center Engineer",
                "company": {"name": "Acme (search-only, should NOT be used)"},
                "location": {"country": "United States", "location": "Reston, VA"},
            },
        ],
    }
    detail_by_id = {
        "bing-001": {
            "hasError": False,
            "id": "bing-001",
            "title": "Data Center Engineer",
            "companyName": "Acme Corp",
            "applyUrl": "https://example.com/bing-apply/001",
            "description": "Bing-sourced JD body...",
        },
    }

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ) as mock_get:
        rows = JobsApi14BingAdapter().fetch(["data center"])

    # 1 search + 1 get
    assert mock_get.call_count == 2
    urls = [call.args[0] for call in mock_get.call_args_list]
    assert urls[0].endswith("/v2/bing/search")
    assert urls[1].endswith("/v2/bing/get")
    # get-call passes the id from the search row
    assert mock_get.call_args_list[1].kwargs["params"]["id"] == "bing-001"

    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Data Center Engineer"
    assert row["location"] == "Reston, VA"
    assert row["api_id"] == "bing-001"
    assert row["source"] == "jobsapi_bing"
    assert row["query"] == "data center"
    # canonical fields come from /v2/bing/get
    assert row["url"] == "https://example.com/bing-apply/001"
    assert row["description"] == "Bing-sourced JD body..."
    assert row["company"] == "Acme Corp"


def test_fetch_uses_companyName_not_company_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit guard for the natural pitfall called out in #765 AC #2:
    `company` only exists on /v2/bing/search; only `companyName` is on
    /v2/bing/get. Using the wrong key here is what caused #601's
    silent-zero-rows bug."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {
                "id": "bing-001",
                "title": "Engineer",
                "location": {"location": "Anywhere"},
                # NOTE: this `company` key is present on the search response
                # but the adapter must NOT consume it — companyName from
                # /v2/bing/get is the canonical field.
                "company": {"name": "WRONG-KEY-VALUE"},
            },
        ],
    }
    detail_by_id = {
        "bing-001": {
            "hasError": False,
            "companyName": "Right Co",
            "applyUrl": "https://example.com/u",
            "description": "JD",
        },
    }

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ):
        rows = JobsApi14BingAdapter().fetch(["q"])

    assert len(rows) == 1
    assert rows[0]["company"] == "Right Co"


def test_fetch_drops_row_when_get_response_omits_applyUrl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If /v2/bing/get returns the record without applyUrl, the row must
    be dropped — the ingest orchestrator would discard it at intake
    anyway, and surfacing partial rows would be misleading."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {"id": "with-url", "title": "Engineer", "location": {"location": "X"}},
            {"id": "no-url", "title": "Engineer", "location": {"location": "X"}},
        ],
    }
    detail_by_id = {
        "with-url": {
            "hasError": False,
            "companyName": "A",
            "applyUrl": "https://example.com/u",
            "description": "d",
        },
        "no-url": {
            "hasError": False,
            "companyName": "B",
            "applyUrl": "",  # missing — drop
            "description": "d",
        },
    }

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ):
        rows = JobsApi14BingAdapter().fetch(["q"])

    assert len(rows) == 1
    assert rows[0]["api_id"] == "with-url"


def test_fetch_drops_rows_with_no_title_or_id_from_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search-stage filters: rows without id or title never reach the
    get-call (saves a wasted RapidAPI request)."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {"id": "", "title": "Engineer", "location": {"location": "X"}},  # no id
            {"id": "y", "title": "", "location": {"location": "X"}},  # no title
            {"id": "z", "title": "Engineer", "location": {"location": "X"}},  # keeper
        ],
    }
    detail_by_id = {
        "z": {
            "hasError": False,
            "companyName": "A",
            "applyUrl": "https://example.com/u",
            "description": "d",
        },
    }

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ) as mock_get:
        rows = JobsApi14BingAdapter().fetch(["q"])

    # 1 search + 1 get (only the keeper triggers a get-call)
    assert mock_get.call_count == 2
    assert len(rows) == 1
    assert rows[0]["api_id"] == "z"


def test_fetch_continues_when_one_get_call_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single failed /v2/bing/get must not kill the rest of the batch.
    The bad row is dropped; the rest stitch normally."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {"id": "ok-1", "title": "Engineer", "location": {"location": "X"}},
            {"id": "bad", "title": "Engineer", "location": {"location": "X"}},
            {"id": "ok-2", "title": "Engineer", "location": {"location": "X"}},
        ],
    }
    detail_by_id = {
        "ok-1": {"hasError": False, "companyName": "A", "applyUrl": "u1", "description": "d"},
        "bad": {"hasError": True, "errors": [{"message": "boom"}]},
        "ok-2": {"hasError": False, "companyName": "C", "applyUrl": "u2", "description": "d"},
    }

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ):
        rows = JobsApi14BingAdapter().fetch(["q"])

    assert {r["api_id"] for r in rows} == {"ok-1", "ok-2"}


def test_fetch_passes_all_titles_through_when_no_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot-zone behavior: AC #4 deferred the title-allowlist decision to a
    post-triage-day measurement. Until that follow-up lands, ALL titles
    flow through (no Indeed-style post-filter). This test locks in the
    allow-all default so a future "while we're here" allowlist add fails
    CI loudly rather than silently dropping rows from operator's stack
    on the next deploy."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {"id": "1", "title": "Cashier", "location": {"location": "X"}},
            {"id": "2", "title": "Senior Data Center Engineer", "location": {"location": "X"}},
            {"id": "3", "title": "Bartender", "location": {"location": "X"}},
        ],
    }
    detail_by_id = {
        "1": {"hasError": False, "companyName": "A", "applyUrl": "u1", "description": "d"},
        "2": {"hasError": False, "companyName": "B", "applyUrl": "u2", "description": "d"},
        "3": {"hasError": False, "companyName": "C", "applyUrl": "u3", "description": "d"},
    }

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ):
        rows = JobsApi14BingAdapter().fetch(["q"])

    titles = {r["title"] for r in rows}
    assert titles == {"Cashier", "Senior Data Center Engineer", "Bartender"}, (
        "AC #4 deferral: Bing must not apply a title allowlist until the "
        "empirical post-triage-day measurement decides one. Allow-all is "
        "the contract; lock it in."
    )


def test_fetch_paces_get_calls_with_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #6: per-row sleep(0.6) between get-calls so a single query's
    18-row response doesn't burst the 2 req/sec PRO ceiling."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {"id": "1", "title": "Engineer", "location": {"location": "X"}},
            {"id": "2", "title": "Engineer", "location": {"location": "X"}},
            {"id": "3", "title": "Engineer", "location": {"location": "X"}},
        ],
    }
    detail_by_id = {
        i: {"hasError": False, "companyName": "A", "applyUrl": f"u{i}", "description": "d"} for i in ("1", "2", "3")
    }
    _mod = "findajob.fetchers.adapters.jobs_api14_bing"
    with (
        patch(f"{_mod}.requests.get", side_effect=_two_call_side_effect(search_payload, detail_by_id)),
        patch(f"{_mod}.time.sleep") as mock_sleep,
    ):
        JobsApi14BingAdapter().fetch(["q"])

    # 3 inter-get pauses (one before each get-call)
    assert mock_sleep.call_count >= 3
    # At least one explicit 0.6s pace
    assert any(call.args == (0.6,) for call in mock_sleep.call_args_list)


def test_fetch_handles_429_with_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    rate_limited.json.return_value = {"hasError": False, "data": []}
    success = _ok_response({"hasError": False, "data": []})

    _mod = "findajob.fetchers.adapters.jobs_api14_bing"
    with patch(f"{_mod}.requests.get", side_effect=[rate_limited, success]) as mock_get:
        JobsApi14BingAdapter().fetch(["engineer"])

    # 429 → retry → 200 empty; no get-call follows because search has no data
    assert mock_get.call_count == 2


def test_fetch_handles_haserror_search_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = _ok_response({"hasError": True, "errors": [{"message": "boom"}], "data": []})

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response):
        rows = JobsApi14BingAdapter().fetch(["engineer"])

    assert rows == []


def test_live_test_auth_failure_with_no_key() -> None:
    result = JobsApi14BingAdapter().live_test(["engineer"])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_success_counts_search_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """live_test stays single-call to /v2/bing/search per query to keep
    onboarding-time spot checks budget-bounded — the get-call is not
    invoked here."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    fake_response = _ok_response(
        {
            "hasError": False,
            "data": [
                {"id": "1", "title": "Engineer", "location": {"location": "X"}},
            ],
        }
    )

    with patch("findajob.fetchers.adapters.jobs_api14_bing.requests.get", return_value=fake_response) as mock_get:
        result = JobsApi14BingAdapter().live_test(["engineer"])

    # Only one call: search, no get
    assert mock_get.call_count == 1
    assert mock_get.call_args.args[0].endswith("/v2/bing/search")
    assert result.ok is True
    assert result.bucket == "success"
    assert len(result.per_query) == 1
    assert result.per_query[0].count == 1
