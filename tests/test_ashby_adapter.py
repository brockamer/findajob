"""Unit tests for AshbyAdapter (#410.2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.ashby import AshbyAdapter


@pytest.fixture
def feed_urls(tmp_path: Path):
    def _write(urls: list[str]) -> str:
        p = tmp_path / "feed_urls.txt"
        p.write_text("\n".join(urls) + "\n")
        return str(p)

    return _write


# ───────────────────── is_configured ─────────────────────


def test_is_configured_true_when_ashby_url_present(feed_urls) -> None:
    adapter = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"]))
    assert adapter.is_configured() is True


def test_is_configured_false_when_no_ashby_url(feed_urls) -> None:
    adapter = AshbyAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"]))
    assert adapter.is_configured() is False


def test_is_configured_false_when_file_missing(tmp_path: Path) -> None:
    assert AshbyAdapter(feed_urls_path=str(tmp_path / "nope.txt")).is_configured() is False


# ───────────────────── fetch() ─────────────────────


def test_fetch_returns_normalized_rows(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            {
                "title": "Software Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/openai/123",
                "location": "San Francisco, CA",
                "descriptionHtml": "<p>Join us.</p>",
            }
        ]
    }
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        rows = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai  # OpenAI"])).fetch([])
    assert len(rows) == 1
    assert rows[0]["source"] == "ashby_json"
    assert rows[0]["title"] == "Software Engineer"
    assert rows[0]["company"] == "OpenAI"
    assert rows[0]["url"] == "https://jobs.ashbyhq.com/openai/123"
    assert rows[0]["location"] == "San Francisco, CA"
    assert "<p>" in rows[0]["description"]


def test_fetch_company_uses_display_name_from_inline_comment(feed_urls) -> None:
    """Ashby's API does not return a company name on each job — the helper's
    display_name (inline comment OR titlecased slug) is the canonical source."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            {
                "title": "Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/openai/9",
                "location": "",
                "descriptionPlain": "",
            }
        ]
    }
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        # No inline comment — display name defaults to titlecased slug ("Openai").
        rows = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).fetch([])
    assert rows[0]["company"] == "Openai"


def test_fetch_handles_dict_shaped_location(feed_urls) -> None:
    """Some Ashby boards return location as `{'name': '...'}`, others as a plain
    string. Adapter must normalize both — preserves legacy fetch_ashby_jobs
    behavior at __init__.py:249-251."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            {
                "title": "Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/openai/1",
                "location": {"name": "Remote"},
                "descriptionHtml": "",
            }
        ]
    }
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        rows = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).fetch([])
    assert rows[0]["location"] == "Remote"


def test_fetch_description_falls_back_to_plain_when_html_absent(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            {
                "title": "Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/openai/1",
                "location": "",
                "descriptionPlain": "Plain text JD.",
            }
        ]
    }
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        rows = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).fetch([])
    assert rows[0]["description"] == "Plain text JD."


def test_fetch_skips_non_200(feed_urls) -> None:
    fake_response = MagicMock(status_code=403)
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        rows = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).fetch([])
    assert rows == []


def test_fetch_retries_once_on_timeout(feed_urls) -> None:
    """Legacy fetch_ashby_jobs retried once on Timeout. Preserve that behavior:
    transient timeouts are common against Ashby's CDN, and a single retry is
    politer than dropping the slug for the day."""
    import requests as req

    ok_response = MagicMock(status_code=200)
    ok_response.json.return_value = {"jobs": []}
    with patch(
        "findajob.fetchers.adapters.ashby.requests.get",
        side_effect=[req.exceptions.Timeout("first"), ok_response],
    ) as mock_get:
        rows = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).fetch([])
    assert mock_get.call_count == 2
    assert rows == []


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": [{"id": 1}, {"id": 2}]}
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        result = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_zero_rows_bucket(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": []}
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        result = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_auth_bucket_on_404(feed_urls) -> None:
    """Ashby has no real auth (public posting API), but invalid slugs return 404.
    Surface as 'auth' bucket so the form renders the same error UI as RapidAPI 401/403."""
    fake_response = MagicMock(status_code=404)
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        result = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/typo-slug"])).live_test([])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_rate_limit_bucket_on_429(feed_urls) -> None:
    """Defensive: Ashby's docs don't promise a 429 response, but if their CDN
    ever returns one we don't want to mis-bucket it as a generic auth failure."""
    fake_response = MagicMock(status_code=429)
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        result = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).live_test([])
    assert result.ok is False
    assert result.bucket == "rate_limit"


def test_live_test_server_bucket_on_5xx(feed_urls) -> None:
    fake_response = MagicMock(status_code=503)
    with patch("findajob.fetchers.adapters.ashby.requests.get", return_value=fake_response):
        result = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).live_test([])
    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket(feed_urls) -> None:
    import requests as req

    with patch(
        "findajob.fetchers.adapters.ashby.requests.get",
        side_effect=req.RequestException("dns fail"),
    ):
        result = AshbyAdapter(feed_urls_path=feed_urls(["https://jobs.ashbyhq.com/openai"])).live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_auth_bucket_when_no_slugs_configured(tmp_path: Path) -> None:
    result = AshbyAdapter(feed_urls_path=str(tmp_path / "nope.txt")).live_test([])
    assert result.ok is False
    assert result.bucket == "auth"
    assert "No Ashby URLs" in (result.auth_error or "")
