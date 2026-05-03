"""JobsApi14IndeedAdapter — restored Indeed coverage via jobs-api14 (#414).

Indeed endpoint exposes no recency / experience-level / employment-type
filters (unlike LinkedIn), so the legacy fetcher (retired pre-#408) returned
~89% off-target rows. This adapter compensates with three knobs:

1. sortType=date — most-recent first, daily triage captures fresh jobs.
2. countryCode=us + location="United States" — geo-narrow.
3. Adapter-side title regex post-filter — inclusion allowlist before storing.
   Configured per-stack via `indeed_title_allow:` in
   `config/prefilter_rules.yaml` (#417). Missing/empty key = no post-filter.

Per-page count is 20 (vs LinkedIn's 10). Description is inline in the
search response, so no separate /v2/linkedin/get-equivalent call needed.

Shares JOBS_API14_KEY / RAPIDAPI_KEY with `JobsApi14Adapter` via the
shared resolver (#414); both adapters are subscriptions on the same
RapidAPI account.
"""

from __future__ import annotations

import time
from typing import ClassVar

import requests

from findajob.cleaning import clean_company, clean_title
from findajob.config_loader import load_indeed_title_allow_rules
from findajob.utils import log_event

from ._keys import resolve_rapidapi_key
from .base import LiveTestResult, QueryResult

__all__ = ("JobsApi14IndeedAdapter",)


class JobsApi14IndeedAdapter:
    """jobs-api14 /v2/indeed/search adapter, tuned for the missing-filter problem."""

    name: ClassVar[str] = "jobs-api14-indeed"
    display_name: ClassVar[str] = "Jobs API — Indeed (jobs-api14)"
    source_label: ClassVar[str] = "jobsapi_indeed"  # preserves DB row continuity
    required_env_vars: ClassVar[tuple[str, ...]] = ("RAPIDAPI_KEY", "JOBS_API14_KEY")

    _ENDPOINT: ClassVar[str] = "https://jobs-api14.p.rapidapi.com/v2/indeed/search"
    _HOST: ClassVar[str] = "jobs-api14.p.rapidapi.com"

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def _api_key(self) -> str:
        return resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY")

    def fetch(self, queries: list[str]) -> list[dict]:
        api_key = self._api_key()
        if not api_key:
            log_event("jobsapi_indeed_error", error="No RAPIDAPI_KEY or JOBS_API14_KEY set in .env")
            return []

        headers = self._headers(api_key)
        rows: list[dict] = []
        last_idx = len(queries) - 1
        for i, query in enumerate(queries):
            data = self._call_with_retry(headers, self._params(query), query)
            if data is None:
                continue
            new_rows = self._parse_rows(data, query)
            rows.extend(new_rows)
            log_event("jobsapi_indeed_fetched", query=query, count=len(new_rows))
            if i < last_idx:
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

        headers = self._headers(api_key)
        per_query: list[QueryResult] = []
        rate_limited = False
        for i, query in enumerate(queries):
            try:
                response = requests.get(self._ENDPOINT, headers=headers, params=self._params(query), timeout=30)
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

            # Apply the same post-filter so live-test counts reflect what would actually ingest
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

    def _params(self, query: str) -> dict[str, str]:
        # Indeed has no datePosted / experienceLevels / employmentTypes filters.
        # Compensating: sortType=date (recency-as-filter) + countryCode=us +
        # location filter. The remaining off-target rows are dropped by the
        # title regex post-filter in _parse_rows.
        return {
            "query": query,
            "location": "United States",
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
                log_event("jobsapi_indeed_error", query=query, errors=data.get("errors"))
                return None
            return data
        except requests.RequestException as e:
            log_event("jobsapi_indeed_error", query=query, error=str(e))
            return None

    def _parse_rows(self, data: dict, query: str) -> list[dict]:
        allow_pattern = load_indeed_title_allow_rules()
        rows: list[dict] = []
        for job in data.get("data", []) or []:
            raw_title = job.get("title", "")
            title = clean_title(raw_title)
            if not title:
                continue
            # Inclusion post-filter — drop titles outside the allowlist.
            # Missing/empty config = allow-all (no filter applied).
            if allow_pattern is not None and not allow_pattern.search(title):
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
                    "description": job.get("description", ""),  # inline JD
                }
            )
        return rows
