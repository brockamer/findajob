"""JobicyAdapter — public Jobicy JSON feed (#853 Phase 2).

Endpoint: `https://jobicy.com/api/v2/remote-jobs`. No auth. ~50 jobs
default, configurable via `?count=`. Per the `friendlyNotice` field in
each response, Jobicy must be credited as the source AND users
redirected to the Jobicy URL to apply — both satisfied by canonical
mapping (`url` field carries the jobicy.com listing URL and
`source_label` makes the source visible).

The notice also discloses a small publication delay (a few hours)
relative to source publication; this is expected and not actionable on
the adapter side.
"""

from __future__ import annotations

import html
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from .base import LiveTestResult, QueryResult


class JobicyAdapter:
    """Jobicy board-feed ingestion via public JSON API.

    Single endpoint, no auth. `queries` parameter is ignored.
    """

    name: ClassVar[str] = "jobicy"
    display_name: ClassVar[str] = "Jobicy"
    source_label: ClassVar[str] = "jobicy_json"
    required_env_vars: ClassVar[tuple[str, ...]] = ()

    _ENDPOINT: ClassVar[str] = "https://jobicy.com/api/v2/remote-jobs"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"
    _DEFAULT_COUNT: ClassVar[int] = 50  # within the documented 1-50 range

    def is_configured(self) -> bool:
        return True

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        url = f"{self._ENDPOINT}?count={self._DEFAULT_COUNT}"
        headers = {"User-Agent": self._UA}
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except Exception as e:
            log_event("jobicy_fetch_error", error=str(e))
            return []
        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", "10")), 60)
            log_event("jobicy_rate_limit", wait=wait)
            time.sleep(wait)
            try:
                resp = requests.get(url, headers=headers, timeout=15)
            except Exception as e:
                log_event("jobicy_fetch_error", error=str(e))
                return []
        if resp.status_code != 200:
            log_event("jobicy_fetch_skip", status=resp.status_code)
            return []
        try:
            payload = resp.json()
        except ValueError as e:
            log_event("jobicy_fetch_invalid_json", error=str(e))
            return []
        if not isinstance(payload, dict) or "jobs" not in payload:
            log_event("jobicy_fetch_invalid_shape", got=type(payload).__name__)
            return []
        rows: list[dict] = []
        for j in payload.get("jobs", []) or []:
            if not isinstance(j, dict):
                continue
            rows.append(
                {
                    "title": clean_title(j.get("jobTitle", "")),
                    "company": clean_company(j.get("companyName", "")),
                    "url": j.get("url", ""),
                    "location": j.get("jobGeo", "") or "",
                    "source": self.source_label,
                    "description": html.unescape(j.get("jobDescription", "") or ""),
                }
            )
        log_event("jobicy_fetch", count=len(rows))
        return rows

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        url = f"{self._ENDPOINT}?count=5"
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
        per_query = [QueryResult(query="all", count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
