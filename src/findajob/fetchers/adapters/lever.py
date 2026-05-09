"""LeverAdapter — migrates fetch_lever_jobs to the JobSourceAdapter framework (#410.3)."""

from __future__ import annotations

import re
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from ._slugs import _parse_feed_slugs
from .base import LiveTestResult, QueryResult


class LeverAdapter:
    """Lever board-feed ingestion via public postings API.

    Lever is a board-feed source, not a query-search source — `queries` on
    fetch() and live_test() is ignored. Slugs are parsed from
    config/feed_urls.txt; one HTTP call per slug. The Lever postings API
    returns a top-level JSON array (not an object with a 'jobs' key like
    Greenhouse/Ashby) — non-list responses are skipped as `unexpected_format`.
    """

    name: ClassVar[str] = "lever"
    display_name: ClassVar[str] = "Lever boards"
    source_label: ClassVar[str] = "lever_json"
    required_env_vars: ClassVar[tuple[str, ...]] = ()  # public API, no key

    _SLUG_RE: ClassVar[re.Pattern] = re.compile(r"lever\.co/([A-Za-z0-9_.-]+)")
    _ENDPOINT_TEMPLATE: ClassVar[str] = "https://api.lever.co/v0/postings/{slug}"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"

    def __init__(self, feed_urls_path: str | None = None) -> None:
        if feed_urls_path is None:
            from findajob.paths import BASE

            feed_urls_path = f"{BASE}/config/feed_urls.txt"
        self._feed_urls_path = feed_urls_path

    def _parse_feeds(self) -> list[tuple[str, str]]:
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
                resp = requests.get(api_url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    log_event("lever_fetch_skip", slug=slug, status=resp.status_code)
                    continue
                lever_jobs = resp.json()
                if not isinstance(lever_jobs, list):
                    log_event("lever_fetch_skip", slug=slug, status="unexpected_format")
                    continue
                for j in lever_jobs:
                    cats = j.get("categories", {})
                    jobs.append(
                        {
                            "title": clean_title(j.get("text", "")),
                            "company": clean_company(display_name),
                            "url": j.get("hostedUrl", ""),
                            "location": cats.get("location", ""),
                            "source": self.source_label,
                            "description": j.get("descriptionPlain", "") or j.get("description", ""),
                        }
                    )
                log_event("lever_fetch", slug=slug, count=len(lever_jobs))
            except Exception as e:
                log_event("lever_fetch_error", slug=slug, error=str(e))
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
                auth_error="No Lever URLs configured in feed_urls.txt.",
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
                auth_error=f"Slug '{slug}' returned 404 — not a valid Lever board.",
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
            data = resp.json()
        except ValueError:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Invalid JSON response.")
        if not isinstance(data, list):
            return LiveTestResult(
                ok=False,
                bucket="server",
                per_query=[],
                auth_error=f"Unexpected format: response was {type(data).__name__}, expected list.",
            )
        count = len(data)
        per_query = [QueryResult(query=slug, count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
