"""Unit tests for RemoteOkAdapter (#853 Phase 1).

Includes a recorded-envelope regression test that exercises the parser
against a real Remote OK API response captured 2026-05-23. Per
feedback_test_real_codepath_when_extracting: synthetic-only fixtures
miss real-world schema quirks; the recorded envelope catches those.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as req

from findajob.fetchers.adapters.remote_ok import RemoteOkAdapter

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "remoteok_envelope.json"


# ───────────────────── is_configured ─────────────────────


def test_is_configured_always_true() -> None:
    """No env vars, no config file — public API is always configured."""
    assert RemoteOkAdapter().is_configured() is True


# ───────────────────── fetch() against recorded envelope ─────────────────────


def test_fetch_parses_recorded_envelope() -> None:
    """End-to-end parse of a real captured Remote OK API response.

    Catches schema regressions the way a synthetic fixture would not —
    real responses carry whitespace, HTML entities, missing fields, and
    metadata-first array shape that synthetic mocks often skip.
    """
    raw = json.loads(_FIXTURE_PATH.read_text())
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = raw
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    # Fixture has 1 metadata + 5 jobs; expect 5 rows back.
    assert len(rows) == 5
    for row in rows:
        assert row["source"] == "remoteok_json"
        assert row["title"]
        assert row["company"]
        assert row["url"].startswith("http")
        assert "location" in row
        assert "description" in row


def test_fetch_skips_metadata_record() -> None:
    """The first element of the response array carries `legal` + `last_updated` —
    not a job. The adapter must NOT yield it as a row, even though it's a dict."""
    raw = json.loads(_FIXTURE_PATH.read_text())
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = raw
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    # The metadata record contains `legal` and `last_updated` keys. Confirm
    # no emitted row carries those (would indicate the metadata leaked through).
    assert all("legal" not in (row.get("description") or "") for row in rows)
    # Negative containment: the literal `last_updated` epoch from the fixture's
    # metadata record (1779557850) must not appear in any field. This catches
    # lazy-regex / off-by-one bugs that would treat the metadata record as a job.
    fixture_meta_epoch = str(raw[0]["last_updated"])
    for row in rows:
        for v in row.values():
            assert fixture_meta_epoch not in str(v)


# ───────────────────── fetch() synthetic happy-path ─────────────────────


def test_fetch_returns_normalized_rows() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [
        {"last_updated": 1, "legal": "..."},
        {
            "position": "Senior Technical Writer",
            "company": "Acme AI",
            "url": "https://remoteok.com/remote-jobs/123-senior-tech-writer-acme-ai",
            "location": "Remote / US",
            "description": "<p>Write docs for our API.</p>",
        },
    ]
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Technical Writer"
    assert rows[0]["company"] == "Acme AI"
    assert rows[0]["url"] == "https://remoteok.com/remote-jobs/123-senior-tech-writer-acme-ai"
    assert rows[0]["location"] == "Remote / US"
    assert rows[0]["source"] == "remoteok_json"
    assert "<p>" in rows[0]["description"]


def test_fetch_handles_missing_optional_fields() -> None:
    """company_logo / tags / salary fields are not consumed — adapter must
    not KeyError when they're absent."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [
        {"last_updated": 1, "legal": "..."},
        {"position": "X", "company": "Y", "url": "https://remoteok.com/remote-jobs/x"},
    ]
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    assert rows[0]["location"] == ""
    assert rows[0]["description"] == ""


def test_fetch_skips_non_dict_entries() -> None:
    """Defensive — if the API ever returns a malformed array element, skip it."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [
        {"last_updated": 1, "legal": "..."},
        "not a dict",
        None,
        {"position": "Valid Job", "company": "Co", "url": "https://remoteok.com/x"},
    ]
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    assert len(rows) == 1
    assert rows[0]["title"] == "Valid Job"


# ───────────────────── fetch() failure modes ─────────────────────


def test_fetch_returns_empty_on_non_200() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_invalid_json() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.side_effect = ValueError("not json")
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_non_list_payload() -> None:
    """Remote OK occasionally returns an error object instead of an array."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"error": "rate limited"}
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        rows = RemoteOkAdapter().fetch([])
    assert rows == []


def test_fetch_returns_empty_on_network_failure() -> None:
    with patch(
        "findajob.fetchers.adapters.remote_ok.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        rows = RemoteOkAdapter().fetch([])
    assert rows == []


def test_fetch_retries_after_429() -> None:
    """First call 429, second call 200 with valid payload — adapter sleeps + retries."""
    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
    ok_response = MagicMock(status_code=200)
    ok_response.json.return_value = [{"legal": "..."}, {"position": "X", "company": "Y", "url": "https://x"}]
    with (
        patch("findajob.fetchers.adapters.remote_ok.requests.get", side_effect=[rate_limited, ok_response]),
        patch("findajob.fetchers.adapters.remote_ok.time.sleep"),
    ):
        rows = RemoteOkAdapter().fetch([])
    assert len(rows) == 1


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [{"legal": "..."}, {"position": "a"}, {"position": "b"}]
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        result = RemoteOkAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2  # 3 records minus metadata


def test_live_test_zero_rows_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [{"legal": "..."}]  # metadata only
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        result = RemoteOkAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_rate_limit_bucket() -> None:
    fake_response = MagicMock(status_code=429)
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        result = RemoteOkAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "rate_limit"


def test_live_test_server_bucket_on_5xx() -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        result = RemoteOkAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket() -> None:
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", side_effect=req.RequestException("conn refused")):
        result = RemoteOkAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_invalid_json_is_server_bucket() -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.side_effect = ValueError("not json")
    with patch("findajob.fetchers.adapters.remote_ok.requests.get", return_value=fake_response):
        result = RemoteOkAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "server"
