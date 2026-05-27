"""Unit tests for GemAdapter (#249)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from findajob.fetchers.adapters.gem import GemAdapter


@pytest.fixture
def feed_urls(tmp_path: Path):
    def _write(urls: list[str]) -> str:
        p = tmp_path / "feed_urls.txt"
        p.write_text("\n".join(urls) + "\n")
        return str(p)

    return _write


def _batch_response(data: dict) -> MagicMock:
    resp = MagicMock(status_code=200)
    resp.json.return_value = [{"data": data}]
    return resp


def _error_response() -> MagicMock:
    resp = MagicMock(status_code=200)
    resp.json.return_value = [{"errors": [{"message": "Something went wrong."}]}]
    return resp


# ───────────────────── is_configured ─────────────────────


def test_is_configured_true_when_gem_url_present(feed_urls) -> None:
    adapter = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/groq"]))
    assert adapter.is_configured() is True


def test_is_configured_false_when_no_gem_url(feed_urls) -> None:
    adapter = GemAdapter(feed_urls_path=feed_urls(["https://boards.greenhouse.io/anthropic"]))
    assert adapter.is_configured() is False


def test_is_configured_false_when_file_missing(tmp_path: Path) -> None:
    assert GemAdapter(feed_urls_path=str(tmp_path / "nope.txt")).is_configured() is False


# ───────────────────── fetch() ─────────────────────


def test_fetch_returns_normalized_rows(feed_urls) -> None:
    list_data = {
        "oatsExternalJobPostings": {
            "jobPostings": [
                {
                    "extId": "abc123",
                    "title": "Software Engineer",
                    "locations": [
                        {"name": "San Francisco, CA", "city": "San Francisco", "isoCountry": "US", "isRemote": False},
                    ],
                    "job": {"locationType": "IN_PERSON", "employmentType": "FULL_TIME"},
                }
            ]
        },
        "jobBoardExternal": {"teamDisplayName": "Groq"},
    }
    detail_data = {
        "oatsExternalJobPosting": {
            "title": "Software Engineer",
            "descriptionHtml": "<p>Join us.</p>",
            "extId": "abc123",
            "locations": [
                {"name": "San Francisco, CA", "city": "San Francisco", "isoCountry": "US", "isRemote": False},
            ],
            "job": {"locationType": "IN_PERSON", "employmentType": "FULL_TIME", "teamDisplayName": "Groq"},
        }
    }

    responses = [_batch_response(list_data), _batch_response(detail_data)]
    with patch("findajob.fetchers.adapters.gem.requests.post", side_effect=responses):
        rows = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/groq  # Groq"])).fetch([])

    assert len(rows) == 1
    assert rows[0]["source"] == "gem_graphql"
    assert rows[0]["title"] == "Software Engineer"
    assert rows[0]["company"] == "Groq"
    assert rows[0]["url"] == "https://jobs.gem.com/groq/abc123"
    assert rows[0]["location"] == "San Francisco"
    assert "<p>" in rows[0]["description"]


def test_fetch_uses_api_company_when_no_inline_comment(feed_urls) -> None:
    list_data = {
        "oatsExternalJobPostings": {
            "jobPostings": [
                {
                    "extId": "xyz",
                    "title": "Engineer",
                    "locations": [],
                    "job": {"locationType": "REMOTE", "employmentType": "FULL_TIME"},
                }
            ]
        },
        "jobBoardExternal": {"teamDisplayName": "Groq"},
    }
    detail_data = {
        "oatsExternalJobPosting": {
            "title": "Engineer",
            "descriptionHtml": "",
            "extId": "xyz",
            "locations": [],
            "job": {"locationType": "REMOTE", "employmentType": "FULL_TIME", "teamDisplayName": "Groq"},
        }
    }

    responses = [_batch_response(list_data), _batch_response(detail_data)]
    with patch("findajob.fetchers.adapters.gem.requests.post", side_effect=responses):
        rows = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/groq"])).fetch([])

    assert rows[0]["company"] == "Groq"


def test_fetch_inline_comment_overrides_api_company(feed_urls) -> None:
    list_data = {
        "oatsExternalJobPostings": {
            "jobPostings": [
                {
                    "extId": "xyz",
                    "title": "Engineer",
                    "locations": [],
                    "job": {"locationType": "REMOTE", "employmentType": "FULL_TIME"},
                }
            ]
        },
        "jobBoardExternal": {"teamDisplayName": "Groq Inc"},
    }
    detail_data = {
        "oatsExternalJobPosting": {
            "title": "Engineer",
            "descriptionHtml": "",
            "extId": "xyz",
            "locations": [],
            "job": {"locationType": "REMOTE", "employmentType": "FULL_TIME", "teamDisplayName": "Groq Inc"},
        }
    }

    responses = [_batch_response(list_data), _batch_response(detail_data)]
    with patch("findajob.fetchers.adapters.gem.requests.post", side_effect=responses):
        rows = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/groq-ai  # Groq"])).fetch([])

    assert rows[0]["company"] == "Groq"


def test_fetch_remote_location(feed_urls) -> None:
    list_data = {
        "oatsExternalJobPostings": {
            "jobPostings": [
                {
                    "extId": "r1",
                    "title": "Engineer",
                    "locations": [{"name": "United States", "city": "", "isoCountry": None, "isRemote": True}],
                    "job": {"locationType": "REMOTE", "employmentType": "FULL_TIME"},
                }
            ]
        },
        "jobBoardExternal": {"teamDisplayName": "Acme"},
    }
    detail_data = {
        "oatsExternalJobPosting": {
            "title": "Engineer",
            "descriptionHtml": "<p>Remote role.</p>",
            "extId": "r1",
            "locations": [{"name": "United States", "city": "", "isoCountry": None, "isRemote": True}],
            "job": {"locationType": "REMOTE", "employmentType": "FULL_TIME", "teamDisplayName": "Acme"},
        }
    }

    responses = [_batch_response(list_data), _batch_response(detail_data)]
    with patch("findajob.fetchers.adapters.gem.requests.post", side_effect=responses):
        rows = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme  # Acme"])).fetch([])

    assert rows[0]["location"] == "Remote"


def test_fetch_skips_posting_without_ext_id(feed_urls) -> None:
    list_data = {
        "oatsExternalJobPostings": {
            "jobPostings": [
                {
                    "extId": "",
                    "title": "Ghost Posting",
                    "locations": [],
                    "job": {"locationType": "IN_PERSON", "employmentType": "FULL_TIME"},
                }
            ]
        },
        "jobBoardExternal": {"teamDisplayName": "Acme"},
    }

    with patch("findajob.fetchers.adapters.gem.requests.post", return_value=_batch_response(list_data)):
        rows = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme"])).fetch([])

    assert rows == []


def test_fetch_survives_detail_failure(feed_urls) -> None:
    list_data = {
        "oatsExternalJobPostings": {
            "jobPostings": [
                {
                    "extId": "abc",
                    "title": "Engineer",
                    "locations": [{"name": "NYC", "city": "New York", "isoCountry": "US", "isRemote": False}],
                    "job": {"locationType": "IN_PERSON", "employmentType": "FULL_TIME"},
                }
            ]
        },
        "jobBoardExternal": {"teamDisplayName": "Acme"},
    }

    responses = [_batch_response(list_data), _error_response()]
    with patch("findajob.fetchers.adapters.gem.requests.post", side_effect=responses):
        rows = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme  # Acme"])).fetch([])

    assert len(rows) == 1
    assert rows[0]["title"] == "Engineer"
    assert rows[0]["description"] == ""
    assert rows[0]["location"] == "New York"


def test_fetch_list_graphql_error_returns_empty(feed_urls) -> None:
    with patch("findajob.fetchers.adapters.gem.requests.post", return_value=_error_response()):
        rows = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme"])).fetch([])

    assert rows == []


# ───────────────────── live_test() buckets ─────────────────────


def test_live_test_success_bucket(feed_urls) -> None:
    data = {
        "oatsExternalJobPostings": {"jobPostings": [{"extId": "1"}, {"extId": "2"}]},
        "jobBoardExternal": {"teamDisplayName": "Acme"},
    }
    with patch("findajob.fetchers.adapters.gem.requests.post", return_value=_batch_response(data)):
        result = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme"])).live_test([])

    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].count == 2


def test_live_test_zero_rows_bucket(feed_urls) -> None:
    data = {
        "oatsExternalJobPostings": {"jobPostings": []},
        "jobBoardExternal": {"teamDisplayName": "Acme"},
    }
    with patch("findajob.fetchers.adapters.gem.requests.post", return_value=_batch_response(data)):
        result = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme"])).live_test([])

    assert result.ok is True
    assert result.bucket == "zero_rows"


def test_live_test_server_bucket_on_graphql_error(feed_urls) -> None:
    with patch("findajob.fetchers.adapters.gem.requests.post", return_value=_error_response()):
        result = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme"])).live_test([])

    assert result.ok is False
    assert result.bucket == "server"


def test_live_test_network_bucket(feed_urls) -> None:
    import requests as req

    with patch(
        "findajob.fetchers.adapters.gem.requests.post",
        side_effect=req.RequestException("dns fail"),
    ):
        result = GemAdapter(feed_urls_path=feed_urls(["https://jobs.gem.com/acme"])).live_test([])

    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_auth_bucket_when_no_slugs_configured(tmp_path: Path) -> None:
    result = GemAdapter(feed_urls_path=str(tmp_path / "nope.txt")).live_test([])
    assert result.ok is False
    assert result.bucket == "auth"
    assert "No Gem URLs" in (result.auth_error or "")
