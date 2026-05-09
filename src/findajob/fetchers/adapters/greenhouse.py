"""GreenhouseAdapter — migrates fetch_greenhouse_jobs to the JobSourceAdapter framework (#410.1)."""

from __future__ import annotations

import html
import re
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from .base import LiveTestResult, QueryResult


class GreenhouseAdapter:
    """Greenhouse board-feed ingestion via public JSON API.

    Greenhouse is a board-feed source, not a query-search source — the
    `queries` parameter on fetch() is ignored. Slugs are parsed from
    config/feed_urls.txt; one HTTP call per slug.
    """

    name: ClassVar[str] = "greenhouse"
    display_name: ClassVar[str] = "Greenhouse boards"
    source_label: ClassVar[str] = "greenhouse_json"
    required_env_vars: ClassVar[tuple[str, ...]] = ()  # public API, no key

    _SLUG_RE: ClassVar[re.Pattern] = re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_.-]+)")
    _ENDPOINT_TEMPLATE: ClassVar[str] = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"

    def __init__(self, feed_urls_path: str | None = None) -> None:
        if feed_urls_path is None:
            from findajob.paths import BASE

            feed_urls_path = f"{BASE}/config/feed_urls.txt"
        self._feed_urls_path = feed_urls_path

    def _parse_slugs(self) -> list[str]:
        """Extract Greenhouse slugs from feed_urls.txt; first occurrence wins."""
        try:
            with open(self._feed_urls_path) as f:
                urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        except FileNotFoundError:
            return []
        seen: set[str] = set()
        slugs: list[str] = []
        for url in urls:
            m = self._SLUG_RE.search(url)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                slugs.append(m.group(1))
        return slugs

    def is_configured(self) -> bool:
        return bool(self._parse_slugs())

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries  # board-feed source — query strings don't apply
        jobs: list[dict] = []
        headers = {"User-Agent": self._UA}
        for slug in self._parse_slugs():
            api_url = self._ENDPOINT_TEMPLATE.format(slug=slug)
            try:
                resp = requests.get(api_url, headers=headers, timeout=15)
                if resp.status_code == 429:
                    wait = min(int(resp.headers.get("Retry-After", "10")), 60)
                    log_event("greenhouse_rate_limit", slug=slug, wait=wait)
                    time.sleep(wait)
                    resp = requests.get(api_url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    log_event("greenhouse_fetch_skip", slug=slug, status=resp.status_code)
                    continue
                gh_jobs = resp.json().get("jobs", [])
                for j in gh_jobs:
                    jobs.append(
                        {
                            "title": clean_title(j.get("title", "")),
                            "company": clean_company(j.get("company_name", "") or slug),
                            "url": j.get("absolute_url", ""),
                            "location": (j.get("location") or {}).get("name", ""),
                            "source": self.source_label,
                            "description": html.unescape(j.get("content", "") or ""),
                        }
                    )
                log_event("greenhouse_fetch", slug=slug, count=len(gh_jobs))
            except Exception as e:
                log_event("greenhouse_fetch_error", slug=slug, error=str(e))
            time.sleep(0.3)
        return jobs

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        slugs = self._parse_slugs()
        if not slugs:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error="No Greenhouse URLs configured in feed_urls.txt.",
            )
        slug = slugs[0]
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
                auth_error=f"Slug '{slug}' returned 404 — not a valid Greenhouse board.",
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
