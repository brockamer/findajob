"""AshbyAdapter — migrates fetch_ashby_jobs to the JobSourceAdapter framework (#410.2)."""

from __future__ import annotations

import re
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from ._slugs import _parse_feed_slugs
from .base import LiveTestResult, QueryResult


class AshbyAdapter:
    """Ashby board-feed ingestion via public posting API.

    Ashby is a board-feed source, not a query-search source — `queries` on
    fetch() and live_test() is ignored. Slugs are parsed from
    config/feed_urls.txt; one HTTP call per slug. Display name comes from the
    inline `# Display Name` comment on the URL line, falling back to the
    titlecased slug.
    """

    name: ClassVar[str] = "ashby"
    display_name: ClassVar[str] = "Ashby boards"
    source_label: ClassVar[str] = "ashby_json"
    required_env_vars: ClassVar[tuple[str, ...]] = ()  # public API, no key

    _SLUG_RE: ClassVar[re.Pattern] = re.compile(r"ashbyhq\.com/([A-Za-z0-9_.-]+)")
    _ENDPOINT_TEMPLATE: ClassVar[str] = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"

    def __init__(self, feed_urls_path: str | None = None) -> None:
        if feed_urls_path is None:
            from findajob.paths import BASE

            feed_urls_path = f"{BASE}/config/feed_urls.txt"
        self._feed_urls_path = feed_urls_path

    def _parse_feeds(self) -> list[tuple[str, str]]:
        """Return (slug, display_name) tuples from feed_urls.txt."""
        return _parse_feed_slugs(self._feed_urls_path, self._SLUG_RE)

    def is_configured(self) -> bool:
        return bool(self._parse_feeds())

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries  # board-feed source — query strings don't apply
        jobs: list[dict] = []
        headers = {"User-Agent": self._UA}
        for slug, display_name in self._parse_feeds():
            api_url = self._ENDPOINT_TEMPLATE.format(slug=slug)
            try:
                try:
                    resp = requests.get(api_url, headers=headers, timeout=30)
                except requests.exceptions.Timeout:
                    log_event("ashby_fetch_retry", slug=slug, reason="timeout")
                    resp = requests.get(api_url, headers=headers, timeout=30)
                if resp.status_code != 200:
                    log_event("ashby_fetch_skip", slug=slug, status=resp.status_code)
                    continue
                ashby_jobs = resp.json().get("jobs", [])
                for j in ashby_jobs:
                    loc = j.get("location") or ""
                    if isinstance(loc, dict):
                        loc = loc.get("name", "")
                    jobs.append(
                        {
                            "title": clean_title(j.get("title", "")),
                            "company": clean_company(display_name),
                            "url": j.get("jobUrl", ""),
                            "location": loc,
                            "source": self.source_label,
                            "description": j.get("descriptionHtml", "") or j.get("descriptionPlain", ""),
                        }
                    )
                log_event("ashby_fetch", slug=slug, count=len(ashby_jobs))
            except Exception as e:
                log_event("ashby_fetch_error", slug=slug, error=str(e))
            time.sleep(0.3)
        return jobs

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        feeds = self._parse_feeds()
        if not feeds:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error="No Ashby URLs configured in feed_urls.txt.",
            )
        slug, _display_name = feeds[0]
        api_url = self._ENDPOINT_TEMPLATE.format(slug=slug)
        try:
            resp = requests.get(api_url, headers={"User-Agent": self._UA}, timeout=15)
        except requests.RequestException as e:
            return LiveTestResult(ok=False, bucket="network", per_query=[], auth_error=str(e))
        if resp.status_code == 404:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error=f"Slug '{slug}' returned 404 — not a valid Ashby board.",
            )
        if resp.status_code == 429:
            return LiveTestResult(ok=False, bucket="rate_limit", per_query=[], auth_error="Rate limited.")
        if 500 <= resp.status_code < 600:
            return LiveTestResult(
                ok=False,
                bucket="server",
                per_query=[],
                auth_error=f"HTTP {resp.status_code}: server error.",
            )
        try:
            count = len(resp.json().get("jobs", []))
        except ValueError:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Invalid JSON response.")
        per_query = [QueryResult(query=slug, count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
