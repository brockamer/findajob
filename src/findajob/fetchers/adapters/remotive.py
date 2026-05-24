"""RemotiveAdapter — public Remotive JSON feed (#853 Phase 2).

Endpoint: `https://remotive.com/api/remote-jobs`. No auth. ~18 jobs per
default fetch (cap not published; `?limit=` not respected on the public
endpoint, so the adapter takes whatever the server returns).

Per the `0-legal-notice` field in every response, attribution requires
linkback to the URL on Remotive AND mention of Remotive as a source —
both satisfied by canonical mapping (the `url` field carries the
remotive.com listing URL and `source_label` makes the source visible).
The notice additionally prohibits re-syndicating jobs to third-party
boards (Jooble, Neuvoo, Google Jobs, LinkedIn Jobs); findajob is not a
board, so the prohibition doesn't apply here.
"""

from __future__ import annotations

import html
import time
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from .base import LiveTestResult, QueryResult


class RemotiveAdapter:
    """Remotive board-feed ingestion via public JSON API.

    Single endpoint, no auth, no per-company enumeration. `queries`
    parameter is ignored (board-feed source).
    """

    name: ClassVar[str] = "remotive"
    display_name: ClassVar[str] = "Remotive"
    source_label: ClassVar[str] = "remotive_json"
    required_env_vars: ClassVar[tuple[str, ...]] = ()

    _ENDPOINT: ClassVar[str] = "https://remotive.com/api/remote-jobs"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"

    def is_configured(self) -> bool:
        return True

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        headers = {"User-Agent": self._UA}
        try:
            resp = requests.get(self._ENDPOINT, headers=headers, timeout=15)
        except Exception as e:
            log_event("remotive_fetch_error", error=str(e))
            return []
        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", "10")), 60)
            log_event("remotive_rate_limit", wait=wait)
            time.sleep(wait)
            try:
                resp = requests.get(self._ENDPOINT, headers=headers, timeout=15)
            except Exception as e:
                log_event("remotive_fetch_error", error=str(e))
                return []
        if resp.status_code != 200:
            log_event("remotive_fetch_skip", status=resp.status_code)
            return []
        try:
            payload = resp.json()
        except ValueError as e:
            log_event("remotive_fetch_invalid_json", error=str(e))
            return []
        if not isinstance(payload, dict) or "jobs" not in payload:
            log_event("remotive_fetch_invalid_shape", got=type(payload).__name__)
            return []
        rows: list[dict] = []
        for j in payload.get("jobs", []) or []:
            if not isinstance(j, dict):
                continue
            rows.append(
                {
                    "title": clean_title(j.get("title", "")),
                    "company": clean_company(j.get("company_name", "")),
                    "url": j.get("url", ""),
                    "location": j.get("candidate_required_location", "") or "",
                    "source": self.source_label,
                    "description": html.unescape(j.get("description", "") or ""),
                }
            )
        log_event("remotive_fetch", count=len(rows))
        return rows

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
        if not isinstance(payload, dict) or "jobs" not in payload:
            return LiveTestResult(
                ok=False, bucket="server", per_query=[], auth_error="Expected object with `jobs` field."
            )
        count = len(payload.get("jobs", []) or [])
        per_query = [QueryResult(query="all", count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
