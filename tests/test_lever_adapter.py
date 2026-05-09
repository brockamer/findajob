"""Unit tests for LeverAdapter (#410.3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.lever import LeverAdapter


@pytest.fixture
def feed_urls(tmp_path: Path):
    def _write(urls: list[str]) -> str:
        p = tmp_path / "feed_urls.txt"
        p.write_text("\n".join(urls) + "\n")
        return str(p)

    return _write


# ───────────────────── is_configured ─────────────────────


def test_is_configured_true_when_lever_url_present(feed_urls) -> None:
    adapter = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"]))
    assert adapter.is_configured() is True


def test_is_configured_false_when_no_lever_url(feed_urls) -> None:
    adapter = LeverAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"]))
    assert adapter.is_configured() is False


def test_is_configured_false_when_file_missing(tmp_path: Path) -> None:
    assert LeverAdapter(feed_urls_path=str(tmp_path / "nope.txt")).is_configured() is False


# ───────────────────── fetch() ─────────────────────


def test_fetch_returns_normalized_rows_from_array_response(feed_urls) -> None:
    """Lever's /v0/postings response is a top-level JSON array, not an object
    with a 'jobs' key — preserves legacy fetch_lever_jobs:181-228 shape."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [
        {
            "text": "Senior Engineer",
            "hostedUrl": "https://jobs.lever.co/zoox/abc-123",
            "categories": {"location": "Foster City, CA"},
            "descriptionPlain": "Build robotaxis.",
        }
    ]
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        rows = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox  # Zoox"])).fetch([])
    assert len(rows) == 1
    assert rows[0]["source"] == "lever_json"
    assert rows[0]["title"] == "Senior Engineer"
    assert rows[0]["company"] == "Zoox"
    assert rows[0]["url"] == "https://jobs.lever.co/zoox/abc-123"
    assert rows[0]["location"] == "Foster City, CA"
    assert rows[0]["description"] == "Build robotaxis."


def test_fetch_company_uses_display_name_from_inline_comment(feed_urls) -> None:
    """Lever's API doesn't return company on each posting — display name is
    canonical (inline comment OR titlecased slug)."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [
        {"text": "Engineer", "hostedUrl": "https://jobs.lever.co/zoox/x", "categories": {}, "descriptionPlain": ""}
    ]
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        rows = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).fetch([])
    assert rows[0]["company"] == "Zoox"  # titlecased slug fallback


def test_fetch_description_falls_back_to_description_when_plain_absent(feed_urls) -> None:
    """Order of preference: descriptionPlain → description (legacy at __init__.py:218)."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [
        {
            "text": "Engineer",
            "hostedUrl": "https://jobs.lever.co/zoox/x",
            "categories": {},
            "descriptionPlain": "",
            "description": "<p>HTML fallback.</p>",
        }
    ]
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        rows = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).fetch([])
    assert rows[0]["description"] == "<p>HTML fallback.</p>"


def test_fetch_skips_non_200(feed_urls) -> None:
    fake_response = MagicMock(status_code=403)
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        rows = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).fetch([])
    assert rows == []


def test_fetch_skips_non_list_response(feed_urls) -> None:
    """Defense against API regression: if Lever ever returns a dict instead of
    an array, skip the slug rather than crashing on iteration. Preserves
    legacy `if not isinstance(lever_jobs, list)` guard at __init__.py:204."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"unexpected": "shape"}
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        rows = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).fetch([])
    assert rows == []


def test_fetch_does_not_retry_on_timeout(feed_urls) -> None:
    """Lever (unlike Ashby) has no Timeout retry in the legacy fetcher.
    Asymmetry preserved — different vendors, different politeness budgets,
    don't add retry loops the legacy code didn't have."""
    import requests as req

    with patch(
        "findajob.fetchers.adapters.lever.requests.get",
        side_effect=req.exceptions.Timeout("once and done"),
    ) as mock_get:
        rows = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).fetch([])
    assert mock_get.call_count == 1
    assert rows == []


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = [{"id": 1}, {"id": 2}]
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        result = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_zero_rows_bucket(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = []
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        result = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_server_bucket_on_non_list_200(feed_urls) -> None:
    """200 + dict-shaped response → server bucket with 'unexpected format'.
    Per AC, this is distinct from the auth/network/server-5xx buckets."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"not": "an array"}
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        result = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).live_test([])
    assert result.ok is False
    assert result.bucket == "server"
    assert "unexpected format" in (result.auth_error or "").lower()


def test_live_test_auth_bucket_on_404(feed_urls) -> None:
    """Lever has no real auth (public posting API), but invalid slugs return 404.
    Surface as 'auth' so the form renders the same error UI as RapidAPI 401/403."""
    fake_response = MagicMock(status_code=404)
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        result = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/typo-slug"])).live_test([])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_rate_limit_bucket_on_429(feed_urls) -> None:
    """Defensive: Lever's docs don't promise a 429 response, but mis-bucketing
    a real one as auth-failure would render the wrong error card."""
    fake_response = MagicMock(status_code=429)
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        result = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).live_test([])
    assert result.ok is False
    assert result.bucket == "rate_limit"


def test_live_test_server_bucket_on_5xx(feed_urls) -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.lever.requests.get", return_value=fake_response):
        result = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket(feed_urls) -> None:
    import requests as req

    with patch(
        "findajob.fetchers.adapters.lever.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        result = LeverAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"])).live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_auth_bucket_when_no_slugs_configured(tmp_path: Path) -> None:
    result = LeverAdapter(feed_urls_path=str(tmp_path / "nope.txt")).live_test([])
    assert result.ok is False
    assert result.bucket == "auth"
    assert "No Lever URLs" in (result.auth_error or "")
