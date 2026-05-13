"""Tests for RapidAPI /v2/linkedin/get 429 handling (issue #223)."""

from unittest.mock import MagicMock

import pytest

from findajob import fetchers
from findajob.fetchers import (
    fetch_linkedin_job_data,
    get_linkedin_rate_limit_stats,
    reset_linkedin_rate_limit_stats,
)


@pytest.fixture(autouse=True)
def _fast_throttle(monkeypatch):
    """Zero out the inter-call sleep so the suite stays fast."""
    monkeypatch.setattr(fetchers, "_LINKEDIN_GET_THROTTLE_SEC", 0)


@pytest.fixture(autouse=True)
def _reset_stats():
    reset_linkedin_rate_limit_stats()
    yield
    reset_linkedin_rate_limit_stats()


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key")


def _mock_ok(description="A real job description, long enough.", company="Acme"):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"data": {"description": description, "companyName": company}}
    r.raise_for_status.return_value = None
    return r


def _mock_429(retry_after="1"):
    r = MagicMock()
    r.status_code = 429
    r.headers = {"Retry-After": retry_after}
    r.json.return_value = {}
    return r


def test_200_success_no_rate_limit(monkeypatch):
    mock_get = MagicMock(return_value=_mock_ok())
    monkeypatch.setattr(fetchers, "time", MagicMock())  # silence any sleeps
    import requests as req

    monkeypatch.setattr(req, "get", mock_get)

    result = fetch_linkedin_job_data("abc123")

    assert result["description"]
    assert result["company"] == "Acme"
    assert mock_get.call_count == 1
    assert get_linkedin_rate_limit_stats() == {"count": 0, "total_wait": 0}


def test_429_then_success_increments_counter_and_retries(monkeypatch):
    sleeps: list[int] = []
    mock_time = MagicMock()
    mock_time.sleep = lambda s: sleeps.append(s)
    monkeypatch.setattr(fetchers, "time", mock_time)

    import requests as req

    mock_get = MagicMock(side_effect=[_mock_429(retry_after="3"), _mock_ok()])
    monkeypatch.setattr(req, "get", mock_get)

    result = fetch_linkedin_job_data("abc123")

    assert result["description"]
    assert mock_get.call_count == 2
    stats = get_linkedin_rate_limit_stats()
    assert stats["count"] == 1
    assert stats["total_wait"] == 3
    assert 3 in sleeps


def test_429_retry_after_clamped_to_60(monkeypatch):
    sleeps: list[int] = []
    mock_time = MagicMock()
    mock_time.sleep = lambda s: sleeps.append(s)
    monkeypatch.setattr(fetchers, "time", mock_time)

    import requests as req

    mock_get = MagicMock(side_effect=[_mock_429(retry_after="9999"), _mock_ok()])
    monkeypatch.setattr(req, "get", mock_get)

    fetch_linkedin_job_data("abc123")

    stats = get_linkedin_rate_limit_stats()
    assert stats["total_wait"] == 60
    assert 60 in sleeps


def test_429_twice_returns_no_description(monkeypatch):
    """Second 429 falls through raise_for_status → logged as linkedin_get_error."""
    mock_time = MagicMock()
    monkeypatch.setattr(fetchers, "time", mock_time)

    import requests as req

    second = _mock_429()
    second.raise_for_status.side_effect = Exception("429")
    mock_get = MagicMock(side_effect=[_mock_429(), second])
    monkeypatch.setattr(req, "get", mock_get)

    result = fetch_linkedin_job_data("abc123")

    assert result == {"description": None, "company": None, "title": None}
    stats = get_linkedin_rate_limit_stats()
    # First 429 incremented the counter; second one bubbles into except.
    assert stats["count"] == 1


def test_counter_reset(monkeypatch):
    mock_time = MagicMock()
    monkeypatch.setattr(fetchers, "time", mock_time)

    import requests as req

    mock_get = MagicMock(side_effect=[_mock_429(), _mock_ok()])
    monkeypatch.setattr(req, "get", mock_get)

    fetch_linkedin_job_data("abc123")
    assert get_linkedin_rate_limit_stats()["count"] == 1

    reset_linkedin_rate_limit_stats()
    assert get_linkedin_rate_limit_stats() == {"count": 0, "total_wait": 0}


def test_stats_snapshot_is_a_copy(monkeypatch):
    """Mutating the returned dict must not corrupt module state."""
    snapshot = get_linkedin_rate_limit_stats()
    snapshot["count"] = 999
    assert get_linkedin_rate_limit_stats()["count"] == 0
