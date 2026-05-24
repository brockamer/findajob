"""RemoteOkAdapter — public Remote OK JSON feed (#853 Phase 1).

Attribution: per the `legal` field at index 0 of the Remote OK API response,
findajob must link back to the Remote OK URL for each job (with follow,
not nofollow) and surface Remote OK as a source. The canonical row mapping
below satisfies both — the `url` field carries the remoteok.com listing page
and `source_label` ("remoteok_json") makes the source visible in the UI.
"""

from __future__ import annotations

import html
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from .base import LiveTestResult, QueryResult


class RemoteOkAdapter:
    """Remote OK board-feed ingestion via public JSON API.

    Single endpoint at `remoteok.com/api`. No auth, no per-company slug
    enumeration — one HTTP call returns the full active board. The first
    record in the response array is metadata (`last_updated`, `legal`);
    indices 1+ are job records. The `queries` parameter is ignored —
    board-feed source, not query-search.
    """

    name: ClassVar[str] = "remote-ok"
    display_name: ClassVar[str] = "Remote OK"
    source_label: ClassVar[str] = "remoteok_json"
    required_env_vars: ClassVar[tuple[str, ...]] = ()  # public API, no key

    _ENDPOINT: ClassVar[str] = "https://remoteok.com/api"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"

    def is_configured(self) -> bool:
        return True

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        headers = {"User-Agent": self._UA}
        try:
            resp = requests.get(self._ENDPOINT, headers=headers, timeout=15)
        except Exception as e:
            log_event("remote_ok_fetch_error", error=str(e))
            return []
        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", "10")), 60)
            log_event("remote_ok_rate_limit", wait=wait)
            time.sleep(wait)
            try:
                resp = requests.get(self._ENDPOINT, headers=headers, timeout=15)
            except Exception as e:
                log_event("remote_ok_fetch_error", error=str(e))
                return []
        if resp.status_code != 200:
            log_event("remote_ok_fetch_skip", status=resp.status_code)
            return []
        try:
            payload = resp.json()
        except ValueError as e:
            log_event("remote_ok_fetch_invalid_json", error=str(e))
            return []
        if not isinstance(payload, list) or len(payload) < 1:
            log_event("remote_ok_fetch_invalid_shape", got=type(payload).__name__)
            return []
        jobs: list[dict] = []
        for j in payload[1:]:
            if not isinstance(j, dict):
                continue
            jobs.append(
                {
                    "title": clean_title(j.get("position", "")),
                    "company": clean_company(j.get("company", "")),
                    "url": j.get("url", ""),
                    "location": j.get("location", "") or "",
                    "source": self.source_label,
                    "description": html.unescape(j.get("description", "") or ""),
                }
            )
        log_event("remote_ok_fetch", count=len(jobs))
        return jobs

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        try:
            resp = requests.get(self._ENDPOINT, headers={"User-Agent": self._UA}, timeout=15)
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
        if not isinstance(payload, list):
            return LiveTestResult(
                ok=False, bucket="server", per_query=[], auth_error="Expected JSON array, got something else."
            )
        count = max(0, len(payload) - 1)  # subtract metadata record at index 0
        per_query = [QueryResult(query="all", count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
