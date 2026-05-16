"""JobsApi14Adapter — refactor of fetch_jobsapi_jobs (#408)."""

from __future__ import annotations

import os
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from ._keys import resolve_rapidapi_key
from ._locations import read_target_locations
from .base import LiveTestResult, QueryResult

# Bind module-level imports so tests can patch them via the public path
__all__ = ("JobsApi14Adapter",)


class JobsApi14Adapter:
    """LinkedIn ingestion via jobs-api14 (RapidAPI).

    fetch() loops up to JOBS_API14_MAX_PAGES pages per query via the
    opaque nextToken pagination contract; live_test() stays single-page
    to keep onboarding-time spot checks budget-bounded (#414 PR2).
    """

    name: ClassVar[str] = "jobs-api14"
    display_name: ClassVar[str] = "Jobs API (jobs-api14)"
    source_label: ClassVar[str] = "jobsapi_linkedin"
    required_env_vars: ClassVar[tuple[str, ...]] = ("RAPIDAPI_KEY", "JOBS_API14_KEY")

    _ENDPOINT: ClassVar[str] = "https://jobs-api14.p.rapidapi.com/v2/linkedin/search"
    _HOST: ClassVar[str] = "jobs-api14.p.rapidapi.com"
    _DEFAULT_MAX_PAGES: ClassVar[int] = 1
    _MAX_PAGES_CEILING: ClassVar[int] = 20

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def _api_key(self) -> str:
        return resolve_rapidapi_key("RAPIDAPI_KEY", "JOBS_API14_KEY")

    @classmethod
    def _max_pages(cls) -> int:
        """Per-stack pagination ceiling. Default 1 (current behavior).

        Set JOBS_API14_MAX_PAGES=N in data/.env to fetch up to N pages per
        query. Each page is one billed RapidAPI request (per-call cost
        confirmed empirically in #414 PR2 probe — 2026-05-03). Recommended
        for PRO-tier stacks; free-tier stacks should leave at 1. Clamped
        to [1, 20] as a defense-in-depth rail.
        """
        raw = os.environ.get("JOBS_API14_MAX_PAGES", "").strip()
        if not raw:
            return cls._DEFAULT_MAX_PAGES
        try:
            value = int(raw)
        except ValueError:
            log_event("jobsapi_max_pages_invalid", value=raw)
            return cls._DEFAULT_MAX_PAGES
        return max(1, min(value, cls._MAX_PAGES_CEILING))

    def fetch(self, queries: list[str]) -> list[dict]:
        api_key = self._api_key()
        if not api_key:
            log_event("jobsapi_error", error="No RAPIDAPI_KEY or JOBS_API14_KEY set in .env")
            return []

        date_posted = _date_posted_for_install()
        log_event("jobsapi_date_posted", value=date_posted)

        max_pages = self._max_pages()
        headers = self._headers(api_key)
        locations = read_target_locations()
        rows: list[dict] = []
        last_loc = len(locations) - 1
        last_q = len(queries) - 1
        for loc_i, location in enumerate(locations):
            for q_i, query in enumerate(queries):
                token: str | None = None
                pages_fetched = 0
                query_rows = 0
                for _page_idx in range(max_pages):
                    # Per the API contract, paginated calls send token alone — the
                    # original query/location params are no-ops once token is set.
                    params = {"token": token} if token is not None else self._params(query, date_posted, location)
                    data = self._call_with_retry(headers, params, query)
                    if data is None:
                        break
                    new_rows = self._parse_rows(data, query)
                    rows.extend(new_rows)
                    query_rows += len(new_rows)
                    pages_fetched += 1
                    token = (data.get("meta") or {}).get("nextToken")
                    if not token:
                        break
                    time.sleep(0.6)  # intra-query pacing for the 2 req/sec PRO ceiling
                log_event(
                    "jobsapi_fetched",
                    source="linkedin",
                    query=query,
                    location=location,
                    count=query_rows,
                    pages=pages_fetched,
                )
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

        date_posted = _date_posted_for_install()
        location = read_target_locations()[0]
        headers = self._headers(api_key)
        per_query: list[QueryResult] = []
        rate_limited = False
        for i, query in enumerate(queries):
            params = self._params(query, date_posted, location)
            try:
                response = requests.get(self._ENDPOINT, headers=headers, params=params, timeout=30)
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

            per_query.append(QueryResult(query=query, count=len(data.get("data", []))))

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

    def _params(self, query: str, date_posted: str, location: str = "United States") -> dict[str, str]:
        return {
            "query": query,
            "location": location,
            "datePosted": date_posted,
            "employmentTypes": "fulltime",
            "experienceLevels": "midSenior;director",
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
            if response.status_code == 403:
                log_event(
                    "jobsapi_403",
                    source="linkedin",
                    query=query,
                    status=403,
                    body_excerpt=response.text[:500],
                    x_ratelimit_requests_remaining=response.headers.get("x-ratelimit-requests-remaining"),
                    x_ratelimit_requests_limit=response.headers.get("x-ratelimit-requests-limit"),
                    retry_after=response.headers.get("retry-after"),
                    x_rapidapi_region=response.headers.get("x-rapidapi-region"),
                )
                return None
            response.raise_for_status()
            data = response.json()
            if data.get("hasError"):
                log_event("jobsapi_error", source="linkedin", query=query, errors=data.get("errors"))
                return None
            return data
        except requests.RequestException as e:
            log_event("jobsapi_error", source="linkedin", query=query, error=str(e))
            return None

    def _parse_rows(self, data: dict, query: str) -> list[dict]:
        rows: list[dict] = []
        for job in data.get("data", []):
            title = clean_title(job.get("title", ""))
            raw_company = job.get("companyName", "") or job.get("company", {})
            if isinstance(raw_company, dict):
                raw_company = raw_company.get("name", "")
            company = clean_company(raw_company)
            loc = job.get("location", "")
            location = loc.get("location", "") if isinstance(loc, dict) else loc
            url = job.get("linkedinUrl", "")
            if not title or not url:
                continue
            rows.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": url,
                    "api_id": str(job.get("id", "")),
                    "source": self.source_label,
                    "query": query,
                }
            )
        return rows


def _date_posted_for_install() -> str:
    """LinkedIn datePosted widened from `day` to `month` for first 30 days post-onboarding."""
    from findajob.paths import BASE

    _NEW_INSTALL_DAYS = 30
    try:
        age_days = (time.time() - os.path.getmtime(f"{BASE}/data/.onboarding-complete")) / 86400
    except OSError:
        return "day"
    return "month" if age_days < _NEW_INSTALL_DAYS else "day"
