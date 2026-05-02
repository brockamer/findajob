"""JobsApi14Adapter — refactor of fetch_jobsapi_jobs (#408)."""

from __future__ import annotations

import os
import time
from typing import ClassVar

import requests

from findajob.cleaning import clean_company, clean_title
from findajob.utils import log_event

from .base import LiveTestResult, QueryResult

# Bind module-level imports so tests can patch them via the public path
__all__ = ("JobsApi14Adapter",)


class JobsApi14Adapter:
    """LinkedIn ingestion via jobs-api14 (RapidAPI)."""

    name: ClassVar[str] = "jobs-api14"
    display_name: ClassVar[str] = "Jobs API (jobs-api14)"
    source_label: ClassVar[str] = "jobsapi_linkedin"
    required_env_vars: ClassVar[tuple[str, ...]] = ("JOBS_API14_KEY",)

    _ENDPOINT: ClassVar[str] = "https://jobs-api14.p.rapidapi.com/v2/linkedin/search"
    _HOST: ClassVar[str] = "jobs-api14.p.rapidapi.com"

    def is_configured(self) -> bool:
        return bool(os.environ.get("JOBS_API14_KEY", ""))

    def fetch(self, queries: list[str]) -> list[dict]:
        api_key = os.environ.get("JOBS_API14_KEY", "")
        if not api_key:
            log_event("jobsapi_error", error="JOBS_API14_KEY not set in .env")
            return []

        date_posted = _date_posted_for_install()
        log_event("jobsapi_date_posted", value=date_posted)

        headers = self._headers(api_key)
        rows: list[dict] = []
        last_idx = len(queries) - 1
        for i, query in enumerate(queries):
            params = self._params(query, date_posted)
            data = self._call_with_retry(headers, params, query)
            if data is None:
                continue
            new_rows = self._parse_rows(data, query)
            rows.extend(new_rows)
            count = len(new_rows)
            log_event("jobsapi_fetched", source="linkedin", query=query, count=count)
            if i < last_idx:
                time.sleep(0.6)
        return rows

    def live_test(self, queries: list[str]) -> LiveTestResult:
        api_key = os.environ.get("JOBS_API14_KEY", "")
        if not api_key:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error="No API key configured.",
            )

        date_posted = _date_posted_for_install()
        headers = self._headers(api_key)
        per_query: list[QueryResult] = []
        rate_limited = False
        for i, query in enumerate(queries):
            params = self._params(query, date_posted)
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

    def _params(self, query: str, date_posted: str) -> dict[str, str]:
        return {
            "query": query,
            "location": "United States",
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
