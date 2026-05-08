"""JSearchAdapter — multi-board aggregator via JSearch (RapidAPI). Closes #310 (#408)."""

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

__all__ = ("JSearchAdapter",)


class JSearchAdapter:
    """Multi-board aggregator (LinkedIn + Indeed + Glassdoor + ZipRecruiter).

    fetch() passes JSEARCH_NUM_PAGES (default 1) as the API's `num_pages`
    param — JSearch handles pagination server-side and returns up to
    N*~10 jobs in one HTTP response, billed as N RapidAPI requests
    (per-call billing — empirically confirmed). live_test() stays at
    num_pages=1 to keep onboarding-time spot checks budget-bounded
    (#414 PR3).
    """

    name: ClassVar[str] = "jsearch"
    display_name: ClassVar[str] = "JSearch"
    source_label: ClassVar[str] = "jsearch"
    required_env_vars: ClassVar[tuple[str, ...]] = ("RAPIDAPI_KEY", "JSEARCH_API_KEY")

    _ENDPOINT: ClassVar[str] = "https://jsearch.p.rapidapi.com/search"
    _HOST: ClassVar[str] = "jsearch.p.rapidapi.com"
    _DEFAULT_NUM_PAGES: ClassVar[int] = 1
    _NUM_PAGES_CEILING: ClassVar[int] = 10

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def _api_key(self) -> str:
        return resolve_rapidapi_key("RAPIDAPI_KEY", "JSEARCH_API_KEY")

    @classmethod
    def _num_pages(cls) -> int:
        """Per-stack num_pages ceiling. Default 1 (current behavior).

        Set JSEARCH_NUM_PAGES=N in data/.env to widen each query's
        server-side pagination. Each page is one billed RapidAPI request
        (per-call cost confirmed empirically in #414 PR3 probe — 2026-05-03;
        2.7x yield for 3x cost). Recommended for PRO-tier stacks; free-tier
        stacks should leave at 1. Clamped to [1, 10] — half of jobs-api14's
        ceiling because JSearch's PRO quota (10k/mo) is half of jobs-api14
        PRO (20k/mo).
        """
        raw = os.environ.get("JSEARCH_NUM_PAGES", "").strip()
        if not raw:
            return cls._DEFAULT_NUM_PAGES
        try:
            value = int(raw)
        except ValueError:
            log_event("jsearch_num_pages_invalid", value=raw)
            return cls._DEFAULT_NUM_PAGES
        return max(1, min(value, cls._NUM_PAGES_CEILING))

    def fetch(self, queries: list[str]) -> list[dict]:
        api_key = self._api_key()
        if not api_key:
            log_event("jsearch_error", error="No RAPIDAPI_KEY or JSEARCH_API_KEY set in .env")
            return []

        num_pages = self._num_pages()
        headers = self._headers(api_key)
        locations = read_target_locations()
        rows: list[dict] = []
        last_loc = len(locations) - 1
        last_q = len(queries) - 1
        for loc_i, location in enumerate(locations):
            for q_i, query in enumerate(queries):
                try:
                    response = requests.get(
                        self._ENDPOINT,
                        headers=headers,
                        params=self._params(query, num_pages, location),
                        timeout=30,
                    )
                    if response.status_code == 429:
                        wait = min(int(response.headers.get("Retry-After", "10")), 60)
                        log_event("jsearch_rate_limit", query=query, wait=wait)
                        time.sleep(wait)
                        response = requests.get(
                            self._ENDPOINT,
                            headers=headers,
                            params=self._params(query, num_pages, location),
                            timeout=30,
                        )
                    response.raise_for_status()
                    data = response.json()
                except (requests.RequestException, ValueError) as e:
                    log_event("jsearch_error", query=query, location=location, error=str(e))
                    if loc_i < last_loc or q_i < last_q:
                        time.sleep(0.6)
                    continue

                new_rows = self._parse_rows(data, query)
                rows.extend(new_rows)
                log_event("jsearch_fetched", query=query, location=location, count=len(new_rows), num_pages=num_pages)
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
                    self._ENDPOINT,
                    headers=headers,
                    params=self._params(query, location=location),
                    timeout=30,
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
        }

    def _params(self, query: str, num_pages: int = 1, location: str = "United States") -> dict[str, str]:
        return {
            "query": query,
            "page": "1",
            "num_pages": str(num_pages),
            "country": "us",
            "location": location,
        }

    def _parse_rows(self, data: dict, query: str) -> list[dict]:
        rows: list[dict] = []
        for job in data.get("data", []) or []:
            title = clean_title(job.get("job_title", ""))
            company = clean_company(job.get("employer_name", ""))
            location_parts = [job.get("job_city", ""), job.get("job_state", "")]
            location = ", ".join([p for p in location_parts if p])
            url = job.get("job_apply_link", "")
            rows.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": url,
                    "api_id": str(job.get("job_id", "")),
                    "source": self.source_label,
                    "query": query,
                }
            )
        return rows
