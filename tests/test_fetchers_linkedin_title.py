"""Tests for the `title` field added to fetch_linkedin_job_data (#656) and
the `_linkedin_title` cache on the job dict consumed by triage.

Two surfaces:

1. `fetch_linkedin_job_data` surfaces `payload["title"]` from the LinkedIn
   `/v2/linkedin/get` response. Previously only description + company were
   extracted, so the title field was paid for and discarded.

2. `fetch_jd`, on the `gmail_linkedin` branch, caches the result's title as
   `job["_linkedin_title"]` — mirrors the existing `_linkedin_company` cache.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from findajob import fetchers
from findajob.fetchers import fetch_jd, fetch_linkedin_job_data


@pytest.fixture(autouse=True)
def _fast_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero the inter-call sleep to keep the suite fast."""
    monkeypatch.setattr(fetchers, "_LINKEDIN_GET_THROTTLE_SEC", 0)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key")


def _ok_resp(
    description: str = "A real job description, well over thirty chars long.",
    company: str = "Acme",
    title: str = "Hardware Reliability Engineer",
) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    r.json.return_value = {"data": {"description": description, "companyName": company, "title": title}}
    r.raise_for_status.return_value = None
    return r


# ── fetch_linkedin_job_data: title surface ───────────────────────────────────


def test_fetch_linkedin_job_data_surfaces_title() -> None:
    """Real API response with a title field is exposed in the return dict."""
    with patch("requests.get", return_value=_ok_resp()):
        result = fetch_linkedin_job_data("4341101773")
    assert result["title"] == "Hardware Reliability Engineer"
    # Pair positive with negative: the title field must not leak the URL,
    # job-ID, or company name (regression guard per #585/#588 pattern).
    assert "linkedin.com" not in (result["title"] or "")
    assert "4341101773" not in (result["title"] or "")
    assert (result["title"] or "") != result["company"]


def test_fetch_linkedin_job_data_missing_title_returns_none() -> None:
    """Payload without a title field → result['title'] is None, not a crash."""
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    r.json.return_value = {"data": {"description": "Long enough description goes here.", "companyName": "Acme"}}
    r.raise_for_status.return_value = None
    with patch("requests.get", return_value=r):
        result = fetch_linkedin_job_data("4341101773")
    assert result["title"] is None
    assert result["company"] == "Acme"  # other fields still work


def test_fetch_linkedin_job_data_error_path_returns_none_title() -> None:
    """hasError response shape must include title=None in the sentinel."""
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    r.json.return_value = {"hasError": True, "errors": ["nope"]}
    r.raise_for_status.return_value = None
    with patch("requests.get", return_value=r):
        result = fetch_linkedin_job_data("4341101773")
    assert result == {"description": None, "company": None, "title": None}


def test_fetch_linkedin_job_data_applies_clean_title() -> None:
    """Title metadata (e.g. trailing 'X days ago') must be stripped by clean_title."""
    # clean_title strips the "via Foo · " and "N days ago" suffixes
    with patch("requests.get", return_value=_ok_resp(title="Hardware Engineer · 3 days ago")):
        result = fetch_linkedin_job_data("4341101773")
    assert result["title"] is not None
    assert "3 days ago" not in result["title"]
    assert result["title"].startswith("Hardware Engineer")


# ── fetch_jd: _linkedin_title cache ──────────────────────────────────────────


def test_fetch_jd_caches_linkedin_title_for_gmail_linkedin() -> None:
    """gmail_linkedin source: fetch_jd populates job['_linkedin_title'] from API."""
    job = {
        "source": "gmail_linkedin",
        "api_id": "4341101773",
        "url": "https://www.linkedin.com/jobs/view/4341101773/",
        "title": "https://www.linkedin.com/jobs/view/4341101773/",  # degenerate, URL as title
        "company": "Lambda",
    }
    with patch("requests.get", return_value=_ok_resp(title="ML Compiler Engineer")):
        jd = fetch_jd(job)
    assert job.get("_linkedin_title") == "ML Compiler Engineer"
    # Positive: real JD text was returned (not the sentinel)
    assert "[LinkedIn JD unavailable" not in jd
    # Negative regression guard: cache must not leak the URL or job-ID
    assert "linkedin.com" not in (job.get("_linkedin_title") or "")
    assert "4341101773" not in (job.get("_linkedin_title") or "")


def test_fetch_jd_does_not_cache_title_for_jobsapi_linkedin() -> None:
    """jobsapi_linkedin uses the same LinkedIn API but does NOT need the cache.

    The cache exists for the gmail_linkedin path where the email parser
    fabricated the title. jobsapi_linkedin gets titles from the search
    endpoint directly — no degeneracy. Skipping the cache write keeps the
    triage degeneracy check from accidentally firing for jobsapi rows that
    legitimately have short or numeric-leading titles.
    """
    job = {
        "source": "jobsapi_linkedin",
        "api_id": "4341101773",
        "url": "https://www.linkedin.com/jobs/view/4341101773/",
        "title": "ML Compiler Engineer",
        "company": "Lambda",
    }
    with patch("requests.get", return_value=_ok_resp(title="ML Compiler Engineer")):
        fetch_jd(job)
    assert "_linkedin_title" not in job


def test_fetch_jd_no_title_in_response_leaves_cache_unset() -> None:
    """API returned no title → cache key is not set (downstream falls back gracefully)."""
    job = {
        "source": "gmail_linkedin",
        "api_id": "4341101773",
        "url": "https://www.linkedin.com/jobs/view/4341101773/",
        "title": "https://www.linkedin.com/jobs/view/4341101773/",
        "company": "Lambda",
    }
    r = MagicMock()
    r.status_code = 200
    r.headers = {}
    # No title field in payload
    r.json.return_value = {"data": {"description": "Job description here, long enough.", "companyName": "Lambda"}}
    r.raise_for_status.return_value = None
    with patch("requests.get", return_value=r):
        fetch_jd(job)
    assert "_linkedin_title" not in job
    # Original (degenerate) title is left untouched — no autofix at fetcher layer;
    # orchestrator owns the decision to swap. This is by design.
    assert job["title"] == "https://www.linkedin.com/jobs/view/4341101773/"
