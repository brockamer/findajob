"""Unit tests for JobicyAdapter (#853 Phase 2).

Includes a recorded-envelope regression test against a real Jobicy JSON
response captured 2026-05-23.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as req

from findajob.fetchers.adapters.jobicy import JobicyAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "jobicy_envelope.json"


def test_is_configured_always_true() -> None:
    assert JobicyAdapter().is_configured() is True


# ───────────────────── fetch() against recorded envelope ─────────────────────


def test_fetch_parses_recorded_envelope() -> None:
    raw = json.loads(_FIXTURE_PATH.read_text())
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = raw
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        rows = JobicyAdapter().fetch([])
    assert len(rows) == 5
    for row in rows:
        assert row["source"] == "jobicy_json"
        assert row["title"]
        assert row["company"]
        assert row["url"].startswith("https://jobicy.com/")
        assert "location" in row
        assert "description" in row


# ───────────────────── fetch() synthetic happy path ─────────────────────


def test_fetch_returns_normalized_rows() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "friendlyNotice": "...",
        "jobs": [
            {
                "jobTitle": "Senior Technical Writer",
                "companyName": "Acme",
                "url": "https://jobicy.com/jobs/123",
                "jobGeo": "USA",
                "jobDescription": "<p>Write docs.</p>",
                "jobType": ["Full-Time"],
            }
        ],
    }
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        rows = JobicyAdapter().fetch([])
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Technical Writer"
    assert rows[0]["company"] == "Acme"
    assert rows[0]["url"] == "https://jobicy.com/jobs/123"
    assert rows[0]["location"] == "USA"


def test_fetch_handles_missing_optional_fields() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": [{"jobTitle": "X", "companyName": "Y", "url": "https://jobicy.com/x"}]}
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        rows = JobicyAdapter().fetch([])
    assert rows[0]["location"] == ""
    assert rows[0]["description"] == ""


def test_fetch_skips_non_dict_entries() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            "bad",
            None,
            {"jobTitle": "Valid", "companyName": "Co", "url": "https://jobicy.com/v"},
        ]
    }
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        rows = JobicyAdapter().fetch([])
    assert len(rows) == 1


# ───────────────────── fetch() failure modes ─────────────────────


def test_fetch_returns_empty_on_non_200() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        rows = JobicyAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_invalid_json() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.side_effect = ValueError("not json")
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        rows = JobicyAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_missing_jobs_field() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"friendlyNotice": "...", "jobCount": 0}
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        rows = JobicyAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_network_failure() -> None:
    with patch(
        "findajob.fetchers.adapters.jobicy.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        rows = JobicyAdapter().fetch([])
    assert rows == []


def test_fetch_retries_after_429() -> None:
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    ok_response = MagicMock(status_code=200)
    ok_response.json.return_value = {"jobs": [{"jobTitle": "X", "companyName": "Y", "url": "https://jobicy.com/x"}]}
    with (
        patch("findajob.fetchers.adapters.jobicy.requests.get", side_effect=[rate_limited, ok_response]),
        patch("findajob.fetchers.adapters.jobicy.time.sleep"),
    ):
        rows = JobicyAdapter().fetch([])
    assert len(rows) == 1


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": [{"jobTitle": "a"}, {"jobTitle": "b"}]}
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        result = JobicyAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "success"


def test_live_test_zero_rows_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": []}
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        result = JobicyAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_rate_limit_bucket() -> None:
    fake_response = MagicMock(status_code=429)
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        result = JobicyAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "rate_limit"


def test_live_test_server_bucket_on_5xx() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        result = JobicyAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket() -> None:
    with patch(
        "findajob.fetchers.adapters.jobicy.requests.get",
        side_effect=req.RequestException("conn refused"),
    ):
        result = JobicyAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_server_bucket_on_missing_jobs() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobCount": 0}
    with patch("findajob.fetchers.adapters.jobicy.requests.get", return_value=fake_response):
        result = JobicyAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"
