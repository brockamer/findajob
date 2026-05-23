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
only come back from `/v2/bing/get?id=<base64_id>`.

**Response envelope** (#765 follow-up, live-captured 2026-05-23): both
endpoints wrap their payload under a top-level `data` key alongside
`hasError`, `errors`, `_links`, etc. The fixtures in this file mirror
that real shape. The original #765 fixtures used a flat shape (synthetic
guess) and passed CI while the adapter produced zero rows in production
— see `_REAL_GET_RESPONSE_2026_05_23` below for a recorded sample that
locks in the real envelope.

Shares JOBS_API14_KEY / RAPIDAPI_KEY with the LinkedIn + Indeed
adapters via the resolver (#414).
"""

from __future__ import annotations

import json
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


def _get_envelope(record: dict | None = None, *, has_error: bool = False) -> dict:
    """Wrap a /v2/bing/get record in its real top-level envelope.

    Real shape: ``{"data": {...record...}, "hasError": false, "_links":
    {...}, "errors": [], "warnings": [], "hasWarning": false}``. Tests
    only care about `data` + `hasError`, so the helper ships those two
    and lets the rest stay absent — `_call_with_retry` and `_compose_row`
    both treat unspecified keys as missing, mirroring the real adapter's
    `.get(...)` contract.
    """
    if has_error:
        return {"hasError": True, "errors": [{"message": "boom"}], "data": None}
    return {"hasError": False, "data": (record or {})}


# Recorded /v2/bing/get response from a live call against the operator's
# stack on 2026-05-23, lightly redacted for length. Locks in the real
# envelope shape so a future test rewrite can't drift back to a flat
# guess. See module docstring + jobs_api14_bing.py module docstring.
_REAL_GET_RESPONSE_2026_05_23: dict = {
    "data": {
        "applyUrl": "https://www.linkedin.com/jobs/view/data-center-technician-united-states-chicago-on-site-at-reboot-monkey-4382621468?trk=bingjobs",
        "companyName": "Rebootmonkey",
        "description": (
            "Job descriptionAbout The Role\nJoin our team as a Data Center Technician in Chicago, United States..."
        ),
        "descriptionHtml": '<div class="jbpnl_descrt_label"><h3>Job description</h3></div>...',
        "employmentType": "Full-time",
        "id": "LTc3ODMxMjEzNS5SZXRybw==",
        "location": "Chicago, IL",
        "postedTimeAgo": "March 7",
        "title": "Data Center Technician - United States - Chicago - On-Site",
    },
    "_links": {"self": {"href": "/v2/bing/get?id=LTc3ODMxMjEzNS5SZXRybw=="}},
    "errors": [],
    "warnings": [],
    "hasError": False,
    "hasWarning": False,
}


def _two_call_side_effect(search_payload: dict, detail_by_id: dict[str, dict]) -> Any:
    """Route `requests.get` calls to /v2/bing/search or /v2/bing/get based
    on the URL argument. Detail responses keyed by `params['id']` — fixtures
    are full envelopes (use `_get_envelope` to build them)."""

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
            payload = detail_by_id.get(params["id"], _get_envelope(has_error=True))
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
    response's nested `data` envelope. Search-only fields (title, location,
    id) come from search."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {
                "id": "bing-001",
                "title": "Data Center Engineer",
                "company": "Acme (search-only, should NOT be used)",
                "location": {"country": "United States", "location": "Reston, VA"},
            },
        ],
    }
    detail_by_id = {
        "bing-001": _get_envelope(
            {
                "id": "bing-001",
                "title": "Data Center Engineer",
                "companyName": "Acme Corp",
                "applyUrl": "https://example.com/bing-apply/001",
                "description": "Bing-sourced JD body...",
            },
        ),
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
    # canonical fields come from /v2/bing/get's `data` envelope
    assert row["url"] == "https://example.com/bing-apply/001"
    assert row["description"] == "Bing-sourced JD body..."
    assert row["company"] == "Acme Corp"


def test_fetch_handles_recorded_real_get_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: locked-in real-shape fixture. The recorded response
    has the full envelope (`_links`, `errors`, `warnings`, `descriptionHtml`,
    `hasError`, `hasWarning`) alongside the canonical fields. If a future
    refactor breaks the unwrap (e.g. moves `applyUrl` lookup back to the
    top level), THIS test fails first — synthetic-shape tests pass against
    the regression but reality doesn't (the original #765 failure mode)."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [
            {
                "id": "LTc3ODMxMjEzNS5SZXRybw==",
                "title": "Data Center Technician - United States - Chicago - On-Site",
                "location": "Chicago, IL",
            },
        ],
    }
    detail_by_id = {"LTc3ODMxMjEzNS5SZXRybw==": _REAL_GET_RESPONSE_2026_05_23}

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ):
        rows = JobsApi14BingAdapter().fetch(["data center"])

    assert len(rows) == 1
    row = rows[0]
    assert row["company"] == "Rebootmonkey"
    assert row["url"].startswith("https://www.linkedin.com/jobs/view/")
    assert "Data Center Technician" in row["description"]
    assert row["api_id"] == "LTc3ODMxMjEzNS5SZXRybw=="


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
                # /v2/bing/get's `data` envelope is the canonical field.
                "company": "WRONG-KEY-VALUE",
            },
        ],
    }
    detail_by_id = {
        "bing-001": _get_envelope(
            {"companyName": "Right Co", "applyUrl": "https://example.com/u", "description": "JD"},
        ),
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
    """If /v2/bing/get returns the `data` envelope without applyUrl, the row
    must be dropped — the ingest orchestrator would discard it at intake
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
        "with-url": _get_envelope(
            {"companyName": "A", "applyUrl": "https://example.com/u", "description": "d"},
        ),
        "no-url": _get_envelope(
            {"companyName": "B", "applyUrl": "", "description": "d"},
        ),
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
            {"id": "", "title": "Engineer", "location": {"location": "X"}},
            {"id": "y", "title": "", "location": {"location": "X"}},
            {"id": "z", "title": "Engineer", "location": {"location": "X"}},
        ],
    }
    detail_by_id = {
        "z": _get_envelope(
            {"companyName": "A", "applyUrl": "https://example.com/u", "description": "d"},
        ),
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
        "ok-1": _get_envelope({"companyName": "A", "applyUrl": "u1", "description": "d"}),
        "bad": _get_envelope(has_error=True),
        "ok-2": _get_envelope({"companyName": "C", "applyUrl": "u2", "description": "d"}),
    }

    with patch(
        "findajob.fetchers.adapters.jobs_api14_bing.requests.get",
        side_effect=_two_call_side_effect(search_payload, detail_by_id),
    ):
        rows = JobsApi14BingAdapter().fetch(["q"])

    assert {r["api_id"] for r in rows} == {"ok-1", "ok-2"}


def test_fetch_logs_unrecognized_response_when_envelope_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When /v2/bing/get returns a body with no `data` key (e.g. the rate-
    limit shape ``{"message": "...exceeded the rate limit per second..."}``
    captured on the operator's stack 2026-05-23), _call_with_retry must
    emit a `jobsapi_bing_unrecognized_response` event AND return None — not
    silently pass through as an empty row (the #601/#765 silent-zero-rows
    failure class)."""
    monkeypatch.setenv("JOBS_API14_KEY", "test-key")
    search_payload = {
        "hasError": False,
        "data": [{"id": "rate-limited-id", "title": "Engineer", "location": {"location": "X"}}],
    }
    # The real shape observed on a per-second rate-limit burst: no `data`
    # envelope, no `hasError`, just a flat `message`.
    rate_limit_body = {"message": "You have exceeded the rate limit per second for your plan, PRO, by the API provider"}
    detail_by_id = {"rate-limited-id": rate_limit_body}

    _mod = "findajob.fetchers.adapters.jobs_api14_bing"
    with (
        patch(f"{_mod}.requests.get", side_effect=_two_call_side_effect(search_payload, detail_by_id)),
        patch(f"{_mod}.log_event") as mock_log,
    ):
        rows = JobsApi14BingAdapter().fetch(["q"])

    assert rows == [], "unrecognized envelope must not produce phantom rows"
    # At least one log call with event=jobsapi_bing_unrecognized_response
    events = [c.args[0] for c in mock_log.call_args_list if c.args]
    assert "jobsapi_bing_unrecognized_response" in events, f"missing loud log for unknown envelope; got events={events}"
    # And the body excerpt must include the rate-limit phrase so the next
    # variant of this shape surfaces with diagnosable context.
    unrecognized_calls = [
        c for c in mock_log.call_args_list if c.args and c.args[0] == "jobsapi_bing_unrecognized_response"
    ]
    assert any("rate limit" in c.kwargs.get("body_excerpt", "").lower() for c in unrecognized_calls)


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
        "1": _get_envelope({"companyName": "A", "applyUrl": "u1", "description": "d"}),
        "2": _get_envelope({"companyName": "B", "applyUrl": "u2", "description": "d"}),
        "3": _get_envelope({"companyName": "C", "applyUrl": "u3", "description": "d"}),
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
        i: _get_envelope({"companyName": "A", "applyUrl": f"u{i}", "description": "d"}) for i in ("1", "2", "3")
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


def test_recorded_get_response_is_real_envelope_shape() -> None:
    """Sanity-check that the recorded fixture preserves the real envelope's
    structural invariants. If someone trims `_links` or `hasWarning` from
    the recorded shape thinking they're noise, the recorded-shape regression
    test stops being a regression test. Keep it whole."""
    keys = set(_REAL_GET_RESPONSE_2026_05_23.keys())
    assert {"data", "hasError", "errors", "warnings", "_links", "hasWarning"} <= keys
    record = _REAL_GET_RESPONSE_2026_05_23["data"]
    assert isinstance(record, dict)
    assert {"applyUrl", "companyName", "description"} <= set(record.keys())
    # And confirm the fixture is JSON-serializable (catches accidental
    # MagicMock or non-JSON values that would diverge from the real wire).
    json.dumps(_REAL_GET_RESPONSE_2026_05_23)
