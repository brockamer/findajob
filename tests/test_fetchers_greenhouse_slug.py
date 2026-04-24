"""Greenhouse slug parsing from feed_urls.txt.

Covers the URL shapes Greenhouse now serves boards under — including the
newer `job-boards.greenhouse.io/{slug}` subdomain and bare-slug (no
trailing path) URLs that the earlier regex silently dropped.
"""

from unittest.mock import MagicMock

import pytest

from findajob import fetchers


@pytest.fixture
def feed_urls(tmp_path):
    def _write(urls):
        p = tmp_path / "feed_urls.txt"
        p.write_text("\n".join(urls) + "\n")
        return str(p)

    return _write


def _empty_board_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"jobs": []}
    return r


def _hit_slugs(monkeypatch):
    """Capture the slug from every Greenhouse API URL fetched."""
    hits: list[str] = []

    def fake_get(url, **_):
        # api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        prefix = "https://boards-api.greenhouse.io/v1/boards/"
        assert url.startswith(prefix), url
        slug = url[len(prefix) :].split("/", 1)[0]
        hits.append(slug)
        return _empty_board_response()

    # fetchers imports `requests as req` inside each function — patching the
    # underlying module attribute works because `req` is the same module object.
    monkeypatch.setattr("requests.get", fake_get)
    return hits


def test_parses_classic_boards_subdomain_with_rss_suffix(feed_urls, monkeypatch):
    hits = _hit_slugs(monkeypatch)
    fetchers.fetch_greenhouse_jobs(feed_urls(["https://boards.greenhouse.io/anthropic/jobs.rss"]))
    assert hits == ["anthropic"]


def test_parses_eu_boards_subdomain(feed_urls, monkeypatch):
    hits = _hit_slugs(monkeypatch)
    fetchers.fetch_greenhouse_jobs(feed_urls(["https://boards.eu.greenhouse.io/somecorp/jobs.rss"]))
    assert hits == ["somecorp"]


def test_parses_job_boards_subdomain_bare_slug(feed_urls, monkeypatch):
    """Newer Greenhouse URL: `job-boards.greenhouse.io/{slug}` with no suffix."""
    hits = _hit_slugs(monkeypatch)
    fetchers.fetch_greenhouse_jobs(feed_urls(["https://job-boards.greenhouse.io/xai"]))
    assert hits == ["xai"]


def test_parses_job_boards_eu_subdomain_bare_slug(feed_urls, monkeypatch):
    hits = _hit_slugs(monkeypatch)
    fetchers.fetch_greenhouse_jobs(feed_urls(["https://job-boards.eu.greenhouse.io/nscaleoperationsukltd"]))
    assert hits == ["nscaleoperationsukltd"]


def test_parses_classic_boards_subdomain_bare_slug(feed_urls, monkeypatch):
    """Classic subdomain with no trailing path — previously dropped silently."""
    hits = _hit_slugs(monkeypatch)
    fetchers.fetch_greenhouse_jobs(feed_urls(["https://boards.greenhouse.io/asteralabs"]))
    assert hits == ["asteralabs"]


def test_dedupes_same_slug_across_url_shapes(feed_urls, monkeypatch):
    """A company listed twice in any URL shape fetches once."""
    hits = _hit_slugs(monkeypatch)
    fetchers.fetch_greenhouse_jobs(
        feed_urls(
            [
                "https://job-boards.greenhouse.io/xai",
                "https://boards.greenhouse.io/xai/jobs.rss",
            ]
        )
    )
    assert hits == ["xai"]


def test_mixed_feed_file_parses_all_shapes(feed_urls, monkeypatch):
    hits = _hit_slugs(monkeypatch)
    fetchers.fetch_greenhouse_jobs(
        feed_urls(
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
    )
    assert set(hits) == {"anthropic", "xai", "nscaleoperationsukltd", "asteralabs"}
