"""Tests for JSearchAdapter (#408 / closes #310)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests as req

from findajob.fetchers.adapters.jsearch import JSearchAdapter


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)


def test_class_attributes() -> None:
    adapter = JSearchAdapter()
    assert adapter.name == "jsearch"
    assert adapter.display_name == "JSearch"
    assert adapter.source_label == "jsearch"
    assert adapter.required_env_vars == ("RAPIDAPI_KEY", "JSEARCH_API_KEY")


def test_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert JSearchAdapter().is_configured() is False
    monkeypatch.setenv("JSEARCH_API_KEY", "k")
    assert JSearchAdapter().is_configured() is True


def test_fetch_hits_correct_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": []}
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake) as mock_get:
        JSearchAdapter().fetch(["nurse practitioner"])
    args, kwargs = mock_get.call_args
    assert args[0] == "https://jsearch.p.rapidapi.com/search"
    assert kwargs["headers"]["x-rapidapi-host"] == "jsearch.p.rapidapi.com"
    assert kwargs["headers"]["x-rapidapi-key"] == "test-key"
    assert kwargs["params"]["query"] == "nurse practitioner"


def test_fetch_parses_jsearch_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSearch returns {data: [{job_title, employer_name, job_city, job_state, job_apply_link, job_id}, ...]}."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {
        "data": [
            {
                "job_id": "ext-1",
                "job_title": "Registered Nurse",
                "employer_name": "Acme Hospital",
                "job_city": "Seattle",
                "job_state": "WA",
                "job_apply_link": "https://acme.com/apply/123",
            },
        ],
    }
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake):
        rows = JSearchAdapter().fetch(["nurse"])
    assert len(rows) == 1
    assert rows[0]["title"] == "Registered Nurse"
    assert rows[0]["company"] == "Acme Hospital"
    assert "Seattle" in rows[0]["location"]
    assert rows[0]["source"] == "jsearch"


def test_live_test_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "good-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": [{"job_title": "RN", "employer_name": "X"}]}
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake):
        result = JSearchAdapter().live_test(["nurse"])
    assert result.ok is True
    assert result.bucket == "success"


def test_live_test_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "bad-key")
    fake = MagicMock(status_code=403, headers={})
    fake.raise_for_status.side_effect = Exception("403")
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake):
        result = JSearchAdapter().live_test(["nurse"])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_zero_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "good-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": []}
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake):
        result = JSearchAdapter().live_test(["nurse", "doctor"])
    assert result.ok is True
    assert result.bucket == "zero_rows"


# --- 4 live_test bucket parity tests (adapted from test_jobs_api14_adapter.py) ---


def test_live_test_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "good-key")
    fake = MagicMock(status_code=503, headers={})
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake):
        result = JSearchAdapter().live_test(["nurse"])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_error_on_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_API_KEY", "good-key")
    with patch(
        "findajob.fetchers.adapters.jsearch.requests.get",
        side_effect=req.ConnectionError("DNS failure"),
    ):
        result = JSearchAdapter().live_test(["nurse", "doctor"])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_network_error_mid_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network error after first query succeeds → partial-result, bucket=rate_limit."""
    monkeypatch.setenv("JSEARCH_API_KEY", "good-key")
    success = MagicMock(status_code=200, headers={})
    success.json.return_value = {"data": [{"job_title": "RN", "employer_name": "X"}]}
    success.raise_for_status.return_value = None
    with patch(
        "findajob.fetchers.adapters.jsearch.requests.get",
        side_effect=[success, req.ConnectionError("DNS failure")],
    ):
        result = JSearchAdapter().live_test(["nurse", "doctor"])
    assert result.ok is True
    assert result.bucket == "rate_limit"
    assert len(result.per_query) == 1


# --- clean_title / clean_company parity test ---


def test_fetch_calls_clean_title_and_clean_company(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row parsing must apply clean_title() and clean_company() like the legacy fetcher."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {
        "data": [
            {
                "job_id": "ext-1",
                "job_title": "Registered Nurse · 3 days ago · 50 applicants",  # raw with appended metadata
                "employer_name": "  Acme Hospital  ",  # leading/trailing whitespace
                "job_city": "Seattle",
                "job_state": "WA",
                "job_apply_link": "https://acme.com/apply/123",
            },
        ],
    }
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake):
        rows = JSearchAdapter().fetch(["nurse"])
    # clean_title strips trailing metadata after ·
    assert "·" not in rows[0]["title"]
    assert "3 days ago" not in rows[0]["title"]
    # clean_company strips whitespace
    assert rows[0]["company"] == "Acme Hospital"


# --- api_id str() cast test ---


def test_fetch_api_id_is_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_id must be str-cast (guard against integer job_id from API response)."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {
        "data": [
            {
                "job_id": 12345,  # integer — API may return this
                "job_title": "Nurse",
                "employer_name": "Hospital",
                "job_city": "LA",
                "job_state": "CA",
                "job_apply_link": "https://example.com/apply/1",
            },
        ],
    }
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake):
        rows = JSearchAdapter().fetch(["nurse"])
    assert rows[0]["api_id"] == "12345"
    assert isinstance(rows[0]["api_id"], str)


# --- pacing test ---


def test_fetch_paces_between_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.6s sleep between successful queries, not before first or after last."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": []}
    fake.raise_for_status.return_value = None

    with (
        patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake),
        patch("findajob.fetchers.adapters.jsearch.time.sleep") as mock_sleep,
    ):
        JSearchAdapter().fetch(["query1", "query2", "query3"])

    sleep_calls = [c.args for c in mock_sleep.call_args_list]
    assert (0.6,) in sleep_calls or any(c[0] == 0.6 for c in sleep_calls)


def test_is_configured_falls_back_to_rapidapi_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared RAPIDAPI_KEY backs JSearchAdapter when JSEARCH_API_KEY is unset (#414)."""
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    assert JSearchAdapter().is_configured() is True


def test_is_configured_canonical_wins_over_dedicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAPIDAPI_KEY is canonical; if both set, RAPIDAPI_KEY wins (#414)."""
    monkeypatch.setenv("RAPIDAPI_KEY", "shared-1234")
    monkeypatch.setenv("JSEARCH_API_KEY", "legacy-1234")
    assert JSearchAdapter().is_configured() is True


# ── #414 PR3 — JSEARCH_NUM_PAGES env var ──


@pytest.fixture
def _scrub_num_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JSEARCH_NUM_PAGES", raising=False)


def test_num_pages_default_is_one(_scrub_num_pages: None) -> None:
    assert JSearchAdapter._num_pages() == 1


def test_num_pages_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_NUM_PAGES", "3")
    assert JSearchAdapter._num_pages() == 3


def test_num_pages_clamps_to_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_NUM_PAGES", "999")
    assert JSearchAdapter._num_pages() == 10


def test_num_pages_clamps_to_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_NUM_PAGES", "0")
    assert JSearchAdapter._num_pages() == 1


def test_num_pages_invalid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JSEARCH_NUM_PAGES", "abc")
    assert JSearchAdapter._num_pages() == 1


def test_fetch_sends_default_num_pages_in_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default JSEARCH_NUM_PAGES=1 → params["num_pages"] == "1" (unchanged behavior)."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.delenv("JSEARCH_NUM_PAGES", raising=False)
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": []}
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake) as mock_get:
        JSearchAdapter().fetch(["nurse"])
    assert mock_get.call_args.kwargs["params"]["num_pages"] == "1"


def test_fetch_sends_configured_num_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSEARCH_NUM_PAGES=3 propagates to the params dict, single HTTP call (per-call billed for 3 units)."""
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setenv("JSEARCH_NUM_PAGES", "3")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": []}
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake) as mock_get:
        JSearchAdapter().fetch(["nurse"])
    assert mock_get.call_count == 1  # still one HTTP request per query
    assert mock_get.call_args.kwargs["params"]["num_pages"] == "3"


def test_live_test_always_sends_num_pages_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """live_test stays single-page even when JSEARCH_NUM_PAGES is raised.

    Onboarding-time connectivity check should not multiply cost.
    """
    monkeypatch.setenv("JSEARCH_API_KEY", "good-key")
    monkeypatch.setenv("JSEARCH_NUM_PAGES", "5")
    fake = MagicMock(status_code=200, headers={})
    fake.json.return_value = {"data": [{"job_title": "X", "employer_name": "Y"}]}
    fake.raise_for_status.return_value = None
    with patch("findajob.fetchers.adapters.jsearch.requests.get", return_value=fake) as mock_get:
        JSearchAdapter().live_test(["q1"])
    assert mock_get.call_args.kwargs["params"]["num_pages"] == "1"
