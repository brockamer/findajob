"""JobsApi14BingAdapter — Bing endpoint via jobs-api14 (#422).

Sibling of `JobsApi14IndeedAdapter` (#414) and `JobsApi14Adapter` (LinkedIn).
The Bing endpoint shares the jobs-api14 RapidAPI credential with both, runs
inline-description ingestion (no separate /v2/bing/get round-trip — same as
Indeed), and pages 18 results per call (vs LinkedIn's 10 / Indeed's 20).

**No title allowlist initially.** AC #4 of #422 calls for an empirical
decision after one triage-day measurement of Bing's natural keyword-match
quality. Tracked in #601. Until that lands, all titles flow through. A
regression test in `tests/test_jobs_api14_bing_adapter.py` locks in this
allow-all contract — adding a post-filter without re-thinking the AC will
fail CI loudly rather than silently dropping rows on the next deploy.

Single-page only — Bing's 18/page already beats LinkedIn's 10. Multi-page
support deferred (no `JOBS_API14_BING_MAX_PAGES` env var) per AC's design
section.

Response-shape assumption: mirrors Indeed's payload (`applyUrl`, `description`
inline, `company.name`, `location.location`). Cannot be verified at PR time
without burning quota; operator's first triage day will surface any
divergence via empty rows or `jobsapi_bing_error` log events. Fast-follow
PR can adjust if real shape differs.
"""

from __future__ import annotations

import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from ._keys import resolve_rapidapi_key
from ._locations import read_target_locations
from .base import LiveTestResult, QueryResult

__all__ = ("JobsApi14BingAdapter",)


class JobsApi14BingAdapter:
    """jobs-api14 /v2/bing/search adapter, default-off opt-in."""

    name: ClassVar[str] = "jobs-api14-bing"
    display_name: ClassVar[str] = "Jobs API — Bing (jobs-api14)"
    source_label: ClassVar[str] = "jobsapi_bing"
    required_env_vars: ClassVar[tuple[str, ...]] = ("RAPIDAPI_KEY", "JOBS_API14_KEY")

    _ENDPOINT: ClassVar[str] = "https://jobs-api14.p.rapidapi.com/v2/bing/search"
    _HOST: ClassVar[str] = "jobs-api14.p.rapidapi.com"

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def _api_key(self) -> str:
        return resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY")

    def fetch(self, queries: list[str]) -> list[dict]:
        api_key = self._api_key()
        if not api_key:
            log_event("jobsapi_bing_error", error="No RAPIDAPI_KEY or JOBS_API14_KEY set in .env")
            return []

        headers = self._headers(api_key)
        locations = read_target_locations()
        rows: list[dict] = []
        last_loc = len(locations) - 1
        last_q = len(queries) - 1
        for loc_i, location in enumerate(locations):
            for q_i, query in enumerate(queries):
                data = self._call_with_retry(headers, self._params(query, location), query)
                if data is None:
                    if loc_i < last_loc or q_i < last_q:
                        time.sleep(0.6)
                    continue
                new_rows = self._parse_rows(data, query)
                rows.extend(new_rows)
                log_event("jobsapi_bing_fetched", query=query, location=location, count=len(new_rows))
                if loc_i < last_loc or q_i < last_q:
                    time.sleep(0.6)
        return rows

    def live_test(self, queries: list[str]) -> LiveTestResult:
        api_key = self._api_key()
        if not api_key:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error="No API key configured.",
            )

        location = read_target_locations()[0]
        headers = self._headers(api_key)
        per_query: list[QueryResult] = []
        rate_limited = False
        for i, query in enumerate(queries):
            try:
                response = requests.get(
                    self._ENDPOINT, headers=headers, params=self._params(query, location), timeout=30
                )
            except requests.RequestException as e:
                if i == 0:
                    return LiveTestResult(ok=False, bucket="network", per_query=[], auth_error=str(e))
                rate_limited = True
                break

            if response.status_code in (401, 403):
                return LiveTestResult(
                    ok=False,
                    bucket="auth",
                    per_query=[],
                    auth_error=f"HTTP {response.status_code}: invalid key or subscription not active.",
                )
            if response.status_code == 429:
                if i == 0:
                    return LiveTestResult(
                        ok=False,
                        bucket="rate_limit",
                        per_query=[],
                        auth_error="Rate limited on first call.",
                    )
                rate_limited = True
                break
            if 500 <= response.status_code < 600:
                return LiveTestResult(
                    ok=False,
                    bucket="server",
                    per_query=[],
                    auth_error=f"HTTP {response.status_code}: server error.",
                )

            try:
                data = response.json()
            except ValueError:
                return LiveTestResult(
                    ok=False,
                    bucket="server",
                    per_query=[],
                    auth_error="Invalid JSON response.",
                )

            if data.get("hasError"):
                return LiveTestResult(
                    ok=False,
                    bucket="auth",
                    per_query=[],
                    auth_error=f"API reported error: {data.get('errors')}.",
                )

            parsed = self._parse_rows(data, query)
            per_query.append(QueryResult(query=query, count=len(parsed)))

        if rate_limited:
            return LiveTestResult(ok=True, bucket="rate_limit", per_query=per_query, auth_error=None)

        total = sum(qr.count for qr in per_query)
        if total == 0:
            return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
        if any(qr.count == 0 for qr in per_query):
            return LiveTestResult(ok=True, bucket="mixed", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)

    # ------------------------- internal helpers -------------------------

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-rapidapi-host": self._HOST,
            "x-rapidapi-key": api_key,
            "Content-Type": "application/json",
        }

    def _params(self, query: str, location: str = "United States") -> dict[str, str]:
        # Conservative params: query + location + countryCode + sortType=date.
        # Bing's per-call filter surface (datePosted, employmentTypes, etc.)
        # is unverified at PR time — start minimal, expand in a fast-follow
        # if operator's triage-day measurement shows we'd benefit from more
        # filters. Same defensive posture used for Indeed at #414 PR1.
        return {
            "query": query,
            "location": location,
            "countryCode": "us",
            "sortType": "date",
        }

    def _call_with_retry(
        self,
        headers: dict[str, str],
        params: dict[str, str],
        query: str,
    ) -> dict | None:
        try:
            response = requests.get(self._ENDPOINT, headers=headers, params=params, timeout=30)
            if response.status_code == 429:
                wait = min(int(response.headers.get("Retry-After", "10")), 60)
                log_event("rapidapi_rate_limit", source=self.name, query=query, wait=wait)
                time.sleep(wait)
                response = requests.get(self._ENDPOINT, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get("hasError"):
                log_event("jobsapi_bing_error", query=query, errors=data.get("errors"))
                return None
            return data
        except requests.RequestException as e:
            log_event("jobsapi_bing_error", query=query, error=str(e))
            return None

    def _parse_rows(self, data: dict, query: str) -> list[dict]:
        # No title allowlist — AC #4 deferral. See module docstring + the
        # `test_fetch_passes_all_titles_through_when_no_allowlist` regression.
        rows: list[dict] = []
        for job in data.get("data", []) or []:
            raw_title = job.get("title", "")
            title = clean_title(raw_title)
            if not title:
                continue

            url = job.get("applyUrl", "")
            if not url:
                continue

            raw_company = job.get("company", {})
            if isinstance(raw_company, dict):
                raw_company = raw_company.get("name", "")
            company = clean_company(raw_company)

            loc = job.get("location", "")
            location = loc.get("location", "") if isinstance(loc, dict) else loc

            rows.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": url,
                    "api_id": str(job.get("id", "")),
                    "source": self.source_label,
                    "query": query,
                    "description": job.get("description", ""),  # inline JD per AC #2
                }
            )
        return rows
