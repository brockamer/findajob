"""Unit tests for HimalayasAdapter (#853 Phase 1).

Includes a recorded-envelope regression test that exercises the parser
against a real Himalayas API response captured 2026-05-23.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as req

from findajob.fetchers.adapters.himalayas import HimalayasAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "himalayas_envelope.json"


# ───────────────────── is_configured ─────────────────────


def test_is_configured_always_true() -> None:
    assert HimalayasAdapter().is_configured() is True


# ───────────────────── fetch() against recorded envelope ─────────────────────


def test_fetch_parses_recorded_envelope() -> None:
    """End-to-end parse of a real captured Himalayas API response."""
    raw = json.loads(_FIXTURE_PATH.read_text())
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = raw
    # 1-page adapter so we only consume the fixture once.
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        rows = HimalayasAdapter(max_pages=1).fetch([])
    assert len(rows) == 5
    for row in rows:
        assert row["source"] == "himalayas_json"
        assert row["title"]
        assert row["company"]
        assert row["url"].startswith("https://himalayas.app/")
        assert "location" in row
        assert "description" in row


def test_fetch_joins_location_restrictions_list() -> None:
    """`locationRestrictions` is an array in Himalayas; the adapter joins
    with ', ' for the canonical row's `location` string."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "totalCount": 1,
        "jobs": [
            {
                "title": "Engineer",
                "companyName": "Co",
                "applicationLink": "https://himalayas.app/companies/co/jobs/eng",
                "locationRestrictions": ["United States", "Canada"],
                "description": "x",
            }
        ],
    }
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        rows = HimalayasAdapter(max_pages=1).fetch([])
    assert rows[0]["location"] == "United States, Canada"


def test_fetch_falls_back_to_guid_when_applicationLink_missing() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "totalCount": 1,
        "jobs": [
            {
                "title": "X",
                "companyName": "Y",
                "applicationLink": "",
                "guid": "https://himalayas.app/companies/y/jobs/x",
                "locationRestrictions": [],
                "description": "",
            }
        ],
    }
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        rows = HimalayasAdapter(max_pages=1).fetch([])
    assert rows[0]["url"] == "https://himalayas.app/companies/y/jobs/x"


# ───────────────────── pagination ─────────────────────


def test_fetch_paginates_across_multiple_pages() -> None:
    """max_pages=3 + non-empty pages → 3 sequential calls, accumulated rows."""
    page = {
        "totalCount": 100,
        "jobs": [
            {
                "title": "Job",
                "companyName": "Co",
                "applicationLink": "https://himalayas.app/x",
                "locationRestrictions": [],
                "description": "",
            }
        ],
    }
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = page
    with (
        patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response) as mock_get,
        patch("findajob.fetchers.adapters.himalayas.time.sleep"),
    ):
        rows = HimalayasAdapter(max_pages=3).fetch([])
    assert len(rows) == 3
    assert mock_get.call_count == 3
    # Verify offset progression: 0, 20, 40
    offsets = [int(c.args[0].split("offset=")[1]) for c in mock_get.call_args_list]
    assert offsets == [0, 20, 40]


def test_fetch_stops_paginating_when_page_is_empty() -> None:
    """If a page returns empty jobs, the adapter stops — don't keep paging
    when the catalog is exhausted."""
    page_with_jobs = {
        "totalCount": 100,
        "jobs": [
            {
                "title": "J",
                "companyName": "C",
                "applicationLink": "https://himalayas.app/x",
                "locationRestrictions": [],
                "description": "",
            }
        ],
    }
    empty_page = {"totalCount": 100, "jobs": []}
    responses = [MagicMock(status_code=200), MagicMock(status_code=200)]
    responses[0].json.return_value = page_with_jobs
    responses[1].json.return_value = empty_page
    with (
        patch("findajob.fetchers.adapters.himalayas.requests.get", side_effect=responses) as mock_get,
        patch("findajob.fetchers.adapters.himalayas.time.sleep"),
    ):
        rows = HimalayasAdapter(max_pages=5).fetch([])
    assert len(rows) == 1
    assert mock_get.call_count == 2  # stopped at empty page


# ───────────────────── fetch() failure modes ─────────────────────


def test_fetch_returns_empty_on_non_200() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        rows = HimalayasAdapter(max_pages=2).fetch([])
    assert rows == []


def test_fetch_returns_empty_on_invalid_json() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.side_effect = ValueError("not json")
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        rows = HimalayasAdapter(max_pages=2).fetch([])
    assert rows == []


def test_fetch_returns_empty_on_missing_jobs_field() -> None:
    """If Himalayas changes its envelope shape, the adapter logs and exits
    rather than crashing."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"comments": "API broken", "totalCount": 0}
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        rows = HimalayasAdapter(max_pages=2).fetch([])
    assert rows == []


def test_fetch_skips_non_dict_job_entries() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "totalCount": 3,
        "jobs": [
            "not a dict",
            None,
            {
                "title": "OK",
                "companyName": "C",
                "applicationLink": "https://himalayas.app/x",
                "locationRestrictions": [],
                "description": "",
            },
        ],
    }
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        rows = HimalayasAdapter(max_pages=1).fetch([])
    assert len(rows) == 1


def test_fetch_returns_empty_on_network_failure() -> None:
    with patch(
        "findajob.fetchers.adapters.himalayas.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        rows = HimalayasAdapter(max_pages=2).fetch([])
    assert rows == []


def test_fetch_retries_after_429() -> None:
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    ok_response = MagicMock(status_code=200)
    ok_response.json.return_value = {
        "totalCount": 1,
        "jobs": [
            {
                "title": "X",
                "companyName": "Y",
                "applicationLink": "https://himalayas.app/x",
                "locationRestrictions": [],
                "description": "",
            }
        ],
    }
    empty = MagicMock(status_code=200)
    empty.json.return_value = {"totalCount": 1, "jobs": []}
    with (
        patch(
            "findajob.fetchers.adapters.himalayas.requests.get",
            side_effect=[rate_limited, ok_response, empty],
        ),
        patch("findajob.fetchers.adapters.himalayas.time.sleep"),
    ):
        rows = HimalayasAdapter(max_pages=5).fetch([])
    assert len(rows) == 1


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"totalCount": 100, "jobs": [{"title": "a"}, {"title": "b"}]}
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        result = HimalayasAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_zero_rows_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"totalCount": 0, "jobs": []}
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        result = HimalayasAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_rate_limit_bucket() -> None:
    fake_response = MagicMock(status_code=429)
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        result = HimalayasAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "rate_limit"


def test_live_test_server_bucket_on_5xx() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        result = HimalayasAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket() -> None:
    with patch(
        "findajob.fetchers.adapters.himalayas.requests.get",
        side_effect=req.RequestException("conn refused"),
    ):
        result = HimalayasAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_server_bucket_on_missing_jobs_field() -> None:
    """If the API envelope changes and `jobs` is missing, surface as server bucket."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"comments": "x"}
    with patch("findajob.fetchers.adapters.himalayas.requests.get", return_value=fake_response):
        result = HimalayasAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"
