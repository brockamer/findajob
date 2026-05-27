"""GemAdapter — Gem GraphQL board-feed ingestion (#249)."""

from __future__ import annotations

import re
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from ._slugs import _parse_feed_slugs
from .base import LiveTestResult, QueryResult

_LIST_QUERY = """\
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings {
      extId
      title
      locations {
        name
        city
        isoCountry
        isRemote
      }
      job {
        locationType
        employmentType
      }
    }
  }
  jobBoardExternal(vanityUrlPath: $boardId) {
    teamDisplayName
  }
}"""

_DETAIL_QUERY = """\
query ExternalJobPostingQuery($boardId: String!, $extId: String!) {
  oatsExternalJobPosting(boardId: $boardId, extId: $extId) {
    title
    descriptionHtml
    extId
    locations {
      name
      city
      isoCountry
      isRemote
    }
    job {
      locationType
      employmentType
      teamDisplayName
    }
  }
}"""


class GemAdapter:
    """Gem board-feed ingestion via public GraphQL batch endpoint.

    Gem is a board-feed source — ``queries`` on fetch() / live_test() is
    ignored. Slugs are parsed from config/feed_urls.txt; two phases per
    slug: one list call, then one detail call per posting (description is
    not on the list response). No auth required.
    """

    name: ClassVar[str] = "gem"
    display_name: ClassVar[str] = "Gem boards"
    source_label: ClassVar[str] = "gem_graphql"
    required_env_vars: ClassVar[tuple[str, ...]] = ()

    _SLUG_RE: ClassVar[re.Pattern] = re.compile(r"jobs\.gem\.com/([A-Za-z0-9_.-]+)")
    _ENDPOINT: ClassVar[str] = "https://jobs.gem.com/api/public/graphql/batch"
    _JOB_URL_TEMPLATE: ClassVar[str] = "https://jobs.gem.com/{slug}/{ext_id}"
    _PER_BOARD_CAP: ClassVar[int] = 500
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

    def _graphql(self, operation: str, query: str, variables: dict, timeout: int = 15) -> dict | None:
        payload = [{"operationName": operation, "variables": variables, "query": query}]
        headers = {"Content-Type": "application/json", "User-Agent": self._UA}
        try:
            resp = requests.post(self._ENDPOINT, json=payload, headers=headers, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            log_event("gem_graphql_error", operation=operation, error=str(e))
            return None
        if resp.status_code != 200:
            log_event("gem_graphql_skip", operation=operation, status=resp.status_code)
            return None
        try:
            batch = resp.json()
        except ValueError:
            log_event("gem_graphql_invalid_json", operation=operation)
            return None
        if not isinstance(batch, list) or not batch:
            return None
        item = batch[0]
        if "errors" in item and not item.get("data"):
            log_event("gem_graphql_errors", operation=operation, errors=str(item["errors"][:1]))
            return None
        return item.get("data")

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        jobs: list[dict] = []
        for slug, display_name in self._parse_feeds():
            jobs.extend(self._fetch_board(slug, display_name))
        return jobs

    def _fetch_board(self, slug: str, display_name_override: str) -> list[dict]:
        data = self._graphql("JobBoardList", _LIST_QUERY, {"boardId": slug}, timeout=30)
        if data is None:
            return []

        postings = (data.get("oatsExternalJobPostings") or {}).get("jobPostings") or []
        api_company = (data.get("jobBoardExternal") or {}).get("teamDisplayName") or ""
        has_explicit_override = display_name_override != slug.title()
        company = clean_company(
            display_name_override if has_explicit_override else (api_company or display_name_override)
        )

        rows: list[dict] = []
        for posting in postings[: self._PER_BOARD_CAP]:
            ext_id = posting.get("extId", "")
            if not ext_id:
                continue

            detail_data = self._graphql(
                "ExternalJobPostingQuery",
                _DETAIL_QUERY,
                {"boardId": slug, "extId": ext_id},
            )
            detail = (detail_data or {}).get("oatsExternalJobPosting") or {}

            location = self._extract_location(detail.get("locations") or posting.get("locations") or [])

            rows.append(
                {
                    "title": clean_title(detail.get("title") or posting.get("title", "")),
                    "company": company,
                    "url": self._JOB_URL_TEMPLATE.format(slug=slug, ext_id=ext_id),
                    "location": location,
                    "source": self.source_label,
                    "description": detail.get("descriptionHtml", ""),
                }
            )
            time.sleep(0.5)

        log_event("gem_fetch", slug=slug, count=len(rows))
        return rows

    @staticmethod
    def _extract_location(locations: list[dict]) -> str:
        if not locations:
            return ""
        loc = locations[0]
        if loc.get("isRemote"):
            return "Remote"
        parts = [loc.get("city", ""), loc.get("name", "")]
        return next((p for p in parts if p), "")

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        feeds = self._parse_feeds()
        if not feeds:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error="No Gem URLs configured in feed_urls.txt.",
            )
        slug, _display_name = feeds[0]
        payload = [{"operationName": "JobBoardList", "variables": {"boardId": slug}, "query": _LIST_QUERY}]
        headers = {"Content-Type": "application/json", "User-Agent": self._UA}
        try:
            resp = requests.post(self._ENDPOINT, json=payload, headers=headers, timeout=15)
        except requests.RequestException as e:
            return LiveTestResult(ok=False, bucket="network", per_query=[], auth_error=str(e))
        if resp.status_code != 200:
            return LiveTestResult(
                ok=False,
                bucket="server",
                per_query=[],
                auth_error=f"HTTP {resp.status_code}: server error.",
            )
        try:
            batch = resp.json()
        except ValueError:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Invalid JSON response.")
        if not isinstance(batch, list) or not batch:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Unexpected response shape.")
        item = batch[0]
        if "errors" in item and not item.get("data"):
            return LiveTestResult(
                ok=False,
                bucket="server",
                per_query=[],
                auth_error="GraphQL request returned errors.",
            )
        data = item.get("data") or {}
        postings = (data.get("oatsExternalJobPostings") or {}).get("jobPostings") or []
        count = len(postings)
        per_query = [QueryResult(query=slug, count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
