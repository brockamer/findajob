"""Unit tests for GreenhouseAdapter (#410.1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.greenhouse import GreenhouseAdapter


@pytest.fixture
def feed_urls(tmp_path: Path):
    def _write(urls: list[str]) -> str:
        p = tmp_path / "feed_urls.txt"
        p.write_text("\n".join(urls) + "\n")
        return str(p)

    return _write


# ───────────────────── is_configured ─────────────────────


def test_is_configured_true_when_greenhouse_url_present(feed_urls) -> None:
    adapter = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"]))
    assert adapter.is_configured() is True


def test_is_configured_false_when_no_greenhouse_url(feed_urls) -> None:
    adapter = GreenhouseAdapter(feed_urls_path=feed_urls(["https://jobs.lever.co/zoox"]))
    assert adapter.is_configured() is False


def test_is_configured_false_when_file_missing(tmp_path: Path) -> None:
    assert GreenhouseAdapter(feed_urls_path=str(tmp_path / "nope.txt")).is_configured() is False


# ───────────────────── slug extraction (7 canonical cases) ─────────────────────


class TestSlugExtraction:
    """Ported verbatim from the now-deleted tests/test_fetchers_greenhouse_slug.py
    so AC #4 (no test loss) is satisfied. These exercise the adapter's private
    _parse_slugs() directly rather than the function-style fetcher."""

    def test_classic_boards_subdomain_with_rss_suffix(self, feed_urls) -> None:
        slugs = GreenhouseAdapter(
            feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic/jobs.rss"])
        )._parse_slugs()
        assert slugs == ["anthropic"]

    def test_eu_boards_subdomain(self, feed_urls) -> None:
        slugs = GreenhouseAdapter(
            feed_urls_path=feed_urls(["https://boards.eu.greenhouse.io/somecorp/jobs.rss"])
        )._parse_slugs()
        assert slugs == ["somecorp"]

    def test_job_boards_subdomain_bare_slug(self, feed_urls) -> None:
        slugs = GreenhouseAdapter(feed_urls_path=feed_urls(["https://job-boards.greenhouse.io/xai"]))._parse_slugs()
        assert slugs == ["xai"]

    def test_job_boards_eu_subdomain_bare_slug(self, feed_urls) -> None:
        slugs = GreenhouseAdapter(
            feed_urls_path=feed_urls(["https://job-boards.eu.greenhouse.io/nscaleoperationsukltd"])
        )._parse_slugs()
        assert slugs == ["nscaleoperationsukltd"]

    def test_classic_boards_subdomain_bare_slug(self, feed_urls) -> None:
        slugs = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/asteralabs"]))._parse_slugs()
        assert slugs == ["asteralabs"]

    def test_dedupes_same_slug_across_url_shapes(self, feed_urls) -> None:
        slugs = GreenhouseAdapter(
            feed_urls_path=feed_urls(
                [
                    "https://job-boards.greenhouse.io/xai",
                    "https://boards.greenhouse.io/xai/jobs.rss",
                ]
            )
        )._parse_slugs()
        assert slugs == ["xai"]

    def test_mixed_feed_file_parses_all_shapes(self, feed_urls) -> None:
        slugs = GreenhouseAdapter(
            feed_urls_path=feed_urls(
                [
                    "# Tier 1",
                    "https://boards.greenhouse.io/anthropic/jobs.rss",
                    "https://job-boards.greenhouse.io/xai",
                    "https://job-boards.eu.greenhouse.io/nscaleoperationsukltd",
                    "https://boards.greenhouse.io/asteralabs",
                    "# non-Greenhouse URL is ignored",
                    "https://jobs.ashbyhq.com/openai",
                ]
            )
        )._parse_slugs()
        assert set(slugs) == {"anthropic", "xai", "nscaleoperationsukltd", "asteralabs"}


# ───────────────────── fetch() happy path ─────────────────────


def test_fetch_returns_normalized_rows(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            {
                "title": "Software Engineer",
                "company_name": "Anthropic",
                "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/123",
                "location": {"name": "San Francisco, CA"},
                "content": "<p>Join us.</p>",
            }
        ]
    }
    with patch("findajob.fetchers.adapters.greenhouse.requests.get", return_value=fake_response):
        rows = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"])).fetch([])
    assert len(rows) == 1
    assert rows[0]["source"] == "greenhouse_json"
    assert rows[0]["title"] == "Software Engineer"
    assert rows[0]["company"] == "Anthropic"
    assert rows[0]["url"] == "https://boards.greenhouse.io/anthropic/jobs/123"
    # Raw HTML preserved at fetch time; pandoc conversion happens later in fetch_jd().
    assert "<p>" in rows[0]["description"]


def test_fetch_company_falls_back_to_slug_when_company_name_missing(feed_urls) -> None:
    """When the API omits company_name (some boards don't populate it), the
    adapter should fall back to the slug — matches the legacy
    fetch_greenhouse_jobs behavior at __init__.py:213."""
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "jobs": [
            {
                "title": "Engineer",
                "company_name": "",
                "absolute_url": "https://boards.greenhouse.io/asteralabs/jobs/9",
                "location": None,
                "content": "",
            }
        ]
    }
    with patch("findajob.fetchers.adapters.greenhouse.requests.get", return_value=fake_response):
        rows = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/asteralabs"])).fetch([])
    assert rows[0]["company"] == "asteralabs"


def test_fetch_skips_non_200(feed_urls) -> None:
    """403/500 responses log and skip rather than raise — matches legacy
    behavior. fetch() returns [] for the only configured slug."""
    fake_response = MagicMock(status_code=403)
    with patch("findajob.fetchers.adapters.greenhouse.requests.get", return_value=fake_response):
        rows = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"])).fetch([])
    assert rows == []


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": [{"id": 1}, {"id": 2}]}
    with patch("findajob.fetchers.adapters.greenhouse.requests.get", return_value=fake_response):
        result = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"])).live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_zero_rows_bucket(feed_urls) -> None:
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"jobs": []}
    with patch("findajob.fetchers.adapters.greenhouse.requests.get", return_value=fake_response):
        result = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"])).live_test([])
    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_auth_bucket_on_404(feed_urls) -> None:
    """Greenhouse is public — no real auth bucket. We surface invalid slugs as
    'auth' so the form renders the same error card it does for RapidAPI 401/403."""
    fake_response = MagicMock(status_code=404)
    with patch("findajob.fetchers.adapters.greenhouse.requests.get", return_value=fake_response):
        result = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/typo-slug"])).live_test([])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_network_bucket(feed_urls) -> None:
    import requests as req

    with patch("findajob.fetchers.adapters.greenhouse.requests.get", side_effect=req.RequestException("dns fail")):
        result = GreenhouseAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"])).live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_auth_bucket_when_no_slugs_configured(tmp_path: Path) -> None:
    """is_configured() being False is upstream of live_test, but live_test should
    still return a structured failure rather than raising if called against an
    unconfigured adapter."""
    result = GreenhouseAdapter(feed_urls_path=str(tmp_path / "nope.txt")).live_test([])
    assert result.ok is False
    assert result.bucket == "auth"
    assert "No Greenhouse URLs" in (result.auth_error or "")
