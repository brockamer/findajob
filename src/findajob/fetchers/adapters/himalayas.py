"""HimalayasAdapter — public Himalayas JSON feed (#853 Phase 1).

Public REST API at `himalayas.app/jobs/api`. No auth. Server caps each
response at 20 jobs regardless of `?limit=` value, so the adapter
paginates via `?offset=N&limit=20` up to a configurable page cap.
Default 5 pages = ~100 listings per fetch, matching Remote OK's order
of magnitude.

The Himalayas catalog mixes employment types (Full Time / Part Time /
Contractor); the adapter does not filter at the fetch layer — downstream
prefilter handles the contract subset. Per the response's `description`
field, every job carries an "Originally posted on Himalayas" linkback in
its HTML body, which satisfies the platform's attribution expectation
implicitly. The adapter additionally surfaces Himalayas as the source via
`source_label = "himalayas_json"`.
"""

from __future__ import annotations

import html
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from .base import LiveTestResult, QueryResult


class HimalayasAdapter:
    """Himalayas board-feed ingestion via public JSON API.

    Single endpoint at `himalayas.app/jobs/api`. No auth, no per-company
    enumeration. The adapter paginates because the API caps at 20 per
    page. `queries` parameter is ignored (board-feed source).
    """

    name: ClassVar[str] = "himalayas"
    display_name: ClassVar[str] = "Himalayas"
    source_label: ClassVar[str] = "himalayas_json"
    required_env_vars: ClassVar[tuple[str, ...]] = ()  # public API, no key

    _ENDPOINT: ClassVar[str] = "https://himalayas.app/jobs/api"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"
    _PAGE_SIZE: ClassVar[int] = 20  # server-side cap
    _DEFAULT_PAGES: ClassVar[int] = 5  # 5 × 20 = 100 listings per fetch

    def __init__(self, max_pages: int | None = None) -> None:
        """`max_pages` controls how many sequential `?offset=` calls happen
        per fetch(). Default 5 pages = ~100 listings; production overrides
        via subclass if needed. Each page is 20 listings server-side."""
        self._max_pages = max_pages if max_pages is not None else self._DEFAULT_PAGES

    def is_configured(self) -> bool:
        return True

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        headers = {"User-Agent": self._UA}
        jobs: list[dict] = []
        for page in range(self._max_pages):
            offset = page * self._PAGE_SIZE
            url = f"{self._ENDPOINT}?limit={self._PAGE_SIZE}&offset={offset}"
            try:
                resp = requests.get(url, headers=headers, timeout=15)
            except Exception as e:
                log_event("himalayas_fetch_error", offset=offset, error=str(e))
                break
            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "10")), 60)
                log_event("himalayas_rate_limit", wait=wait)
                time.sleep(wait)
                try:
                    resp = requests.get(url, headers=headers, timeout=15)
                except Exception as e:
                    log_event("himalayas_fetch_error", offset=offset, error=str(e))
                    break
            if resp.status_code != 200:
                log_event("himalayas_fetch_skip", offset=offset, status=resp.status_code)
                break
            try:
                payload = resp.json()
            except ValueError as e:
                log_event("himalayas_fetch_invalid_json", offset=offset, error=str(e))
                break
            if not isinstance(payload, dict) or "jobs" not in payload:
                log_event("himalayas_fetch_invalid_shape", got=type(payload).__name__)
                break
            page_jobs = payload.get("jobs", [])
            if not isinstance(page_jobs, list) or not page_jobs:
                # Empty page = end of catalog; stop paginating.
                break
            for j in page_jobs:
                if not isinstance(j, dict):
                    continue
                location = j.get("locationRestrictions") or []
                location_str = ", ".join(location) if isinstance(location, list) else str(location)
                jobs.append(
                    {
                        "title": clean_title(j.get("title", "")),
                        "company": clean_company(j.get("companyName", "")),
                        "url": j.get("applicationLink", "") or j.get("guid", ""),
                        "location": location_str,
                        "source": self.source_label,
                        "description": html.unescape(j.get("description", "") or ""),
                    }
                )
            time.sleep(0.3)  # polite pagination
        log_event("himalayas_fetch", count=len(jobs))
        return jobs

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        url = f"{self._ENDPOINT}?limit={self._PAGE_SIZE}"
        try:
            resp = requests.get(url, headers={"User-Agent": self._UA}, timeout=15)
        except requests.RequestException as e:
            return LiveTestResult(ok=False, bucket="network", per_query=[], auth_error=str(e))
        if resp.status_code == 429:
            return LiveTestResult(ok=False, bucket="rate_limit", per_query=[], auth_error="Rate limited.")
        if 500 <= resp.status_code < 600:
            return LiveTestResult(
                ok=False, bucket="server", per_query=[], auth_error=f"HTTP {resp.status_code}: server error."
            )
        if resp.status_code != 200:
            return LiveTestResult(
                ok=False,
                bucket="server",
                per_query=[],
                auth_error=f"HTTP {resp.status_code}: unexpected response.",
            )
        try:
            payload = resp.json()
        except ValueError:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Invalid JSON response.")
        if not isinstance(payload, dict) or "jobs" not in payload:
            return LiveTestResult(
                ok=False, bucket="server", per_query=[], auth_error="Expected object with `jobs` field."
            )
        count = len(payload.get("jobs", []) or [])
        per_query = [QueryResult(query="page 1", count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
