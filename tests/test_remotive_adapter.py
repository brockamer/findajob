"""Unit tests for RemotiveAdapter (#853 Phase 2).

Includes a recorded-envelope regression test against a real Remotive
JSON response captured 2026-05-23.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as req

from findajob.fetchers.adapters.remotive import RemotiveAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "remotive_envelope.json"


def test_is_configured_always_true() -> None:
    assert RemotiveAdapter().is_configured() is True


# ───────────────────── fetch() against recorded envelope ─────────────────────


def test_fetch_parses_recorded_envelope() -> None:
    raw = json.loads(_FIXTURE_PATH.read_text())
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = raw
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        rows = RemotiveAdapter().fetch([])
    assert len(rows) == 5
    for row in rows:
        assert row["source"] == "remotive_json"
        assert row["title"]
        assert row["company"]
        assert row["url"].startswith("https://remotive.com/")
        assert "location" in row
        assert "description" in row


# ───────────────────── fetch() synthetic happy path ─────────────────────


def test_fetch_returns_normalized_rows() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "0-legal-notice": "...",
        "jobs": [
            {
                "title": "Senior Documentation Engineer",
                "company_name": "Acme",
                "url": "https://remotive.com/remote-jobs/x",
                "candidate_required_location": "Worldwide",
                "description": "<p>Write docs.</p>",
                "job_type": "contract",
            }
        ],
    }
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        rows = RemotiveAdapter().fetch([])
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Documentation Engineer"
    assert rows[0]["company"] == "Acme"
    assert rows[0]["url"] == "https://remotive.com/remote-jobs/x"
    assert rows[0]["location"] == "Worldwide"


def test_fetch_handles_missing_location_field() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": [{"title": "X", "company_name": "Y", "url": "https://remotive.com/x"}]}
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        rows = RemotiveAdapter().fetch([])
    assert rows[0]["location"] == ""


def test_fetch_skips_non_dict_entries() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            "not a dict",
            None,
            {"title": "Valid", "company_name": "Co", "url": "https://remotive.com/v"},
        ]
    }
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        rows = RemotiveAdapter().fetch([])
    assert len(rows) == 1
    assert rows[0]["title"] == "Valid"


# ───────────────────── fetch() failure modes ─────────────────────


def test_fetch_returns_empty_on_non_200() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        rows = RemotiveAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_invalid_json() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.side_effect = ValueError("not json")
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        rows = RemotiveAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_missing_jobs_field() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"0-legal-notice": "...", "job-count": 0}
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        rows = RemotiveAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_network_failure() -> None:
    with patch(
        "findajob.fetchers.adapters.remotive.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        rows = RemotiveAdapter().fetch([])
    assert rows == []


def test_fetch_retries_after_429() -> None:
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    ok_response = MagicMock(status_code=200)
    ok_response.json.return_value = {"jobs": [{"title": "X", "company_name": "Y", "url": "https://remotive.com/x"}]}
    with (
        patch("findajob.fetchers.adapters.remotive.requests.get", side_effect=[rate_limited, ok_response]),
        patch("findajob.fetchers.adapters.remotive.time.sleep"),
    ):
        rows = RemotiveAdapter().fetch([])
    assert len(rows) == 1


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": [{"title": "a"}, {"title": "b"}]}
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        result = RemotiveAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_zero_rows_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": []}
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        result = RemotiveAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_rate_limit_bucket() -> None:
    fake_response = MagicMock(status_code=429)
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        result = RemotiveAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "rate_limit"


def test_live_test_server_bucket_on_5xx() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        result = RemotiveAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket() -> None:
    with patch(
        "findajob.fetchers.adapters.remotive.requests.get",
        side_effect=req.RequestException("conn refused"),
    ):
        result = RemotiveAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_server_bucket_on_missing_jobs() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"job-count": 0}
    with patch("findajob.fetchers.adapters.remotive.requests.get", return_value=fake_response):
        result = RemotiveAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"
