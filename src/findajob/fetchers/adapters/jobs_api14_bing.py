"""JobsApi14BingAdapter — Bing endpoint via jobs-api14 (#422, #765).

Sibling of `JobsApi14IndeedAdapter` (#414) and `JobsApi14Adapter` (LinkedIn).
All three share the jobs-api14 RapidAPI credential via the resolver (#414).

**Two-call shape.** `/v2/bing/search` returns lightweight summary rows;
the full record — including `applyUrl`, `description`, and `companyName` —
only comes back from `/v2/bing/get?id=<base64_id>`. `fetch()` does the
fan-out inline so the adapter returns fully-formed rows to the ingest
layer.

**Response envelope.** Both endpoints wrap the payload under a top-level
`data` key (verified via live capture 2026-05-23 — see #765 follow-up).
Search returns `{"data": [<summary>, ...], "hasError": false, ...}`; get
returns `{"data": {<record>}, "hasError": false, ...}`. Unrecognized
envelopes (e.g. `{"message": "...rate limit..."}` seen on transient PRO-
tier bursts) emit a distinct `jobsapi_bing_unrecognized_response` log
event rather than silently passing through as empty rows.

Adapter cousins for context:
- Indeed has `applyUrl` + `description` inline from a single search call.
- LinkedIn returns its URL inline; the LinkedIn get-call exists too but
  fetches the JD body only, and runs later out of `findajob.fetchers.fetch_jd`.
- Bing is the odd one out — both URL *and* JD come from get. The get-call
  lives inside this adapter rather than in `fetch_jd()` because the ingest
  orchestrator drops rows with empty `url` at intake; deferring the
  get-call would mean every Bing row gets discarded before `fetch_jd` ever
  sees it (#765 design rationale).

Cost shape per query × location: 1 search call + up to 18 get calls
(Bing's per-page ceiling). Each call paced by `time.sleep(0.6)` to stay
under the PRO-tier 2 req/sec budget.

**No title allowlist initially.** AC #4 of #422 calls for an empirical
decision after one triage-day measurement of Bing's natural keyword-match
quality. Until that lands, all titles flow through. A regression test in
`tests/test_jobs_api14_bing_adapter.py` locks in this allow-all contract.

Single-page only — Bing's 18/page already beats LinkedIn's 10. Multi-page
support deferred (no `JOBS_API14_BING_MAX_PAGES` env var) per AC's design
section.
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
    """jobs-api14 /v2/bing/search + /v2/bing/get adapter, default-off opt-in."""

    name: ClassVar[str] = "jobs-api14-bing"
    display_name: ClassVar[str] = "Jobs API — Bing (jobs-api14)"
    source_label: ClassVar[str] = "jobsapi_bing"
    required_env_vars: ClassVar[tuple[str, ...]] = ("RAPIDAPI_KEY", "JOBS_API14_KEY")

    _SEARCH_ENDPOINT: ClassVar[str] = "https://jobs-api14.p.rapidapi.com/v2/bing/search"
    _GET_ENDPOINT: ClassVar[str] = "https://jobs-api14.p.rapidapi.com/v2/bing/get"
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
                data = self._call_with_retry(self._SEARCH_ENDPOINT, headers, self._params(query, location), query)
                if data is None:
                    if loc_i < last_loc or q_i < last_q:
                        time.sleep(0.6)
                    continue
                hints = self._parse_search_rows(data)
                detail_rows: list[dict] = []
                for hint in hints:
                    time.sleep(0.6)
                    detail = self._fetch_detail(headers, hint["id"], query)
                    if detail is None:
                        continue
                    row = self._compose_row(hint, detail, query)
                    if row is not None:
                        detail_rows.append(row)
                rows.extend(detail_rows)
                log_event(
                    "jobsapi_bing_fetched",
                    query=query,
                    location=location,
                    count=len(detail_rows),
                    search_rows=len(hints),
                )
                if loc_i < last_loc or q_i < last_q:
                    time.sleep(0.6)
        return rows

    def live_test(self, queries: list[str]) -> LiveTestResult:
        # Single search call per query — onboarding-time spot check stays
        # budget-bounded. Counts reflect search-shape hits; the get-call
        # path is covered by unit tests + the post-fix live-validation
        # documented in #765's "How to verify post-fix" block.
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
                    self._SEARCH_ENDPOINT, headers=headers, params=self._params(query, location), timeout=30
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

            hints = self._parse_search_rows(data)
            per_query.append(QueryResult(query=query, count=len(hints)))

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
        url: str,
        headers: dict[str, str],
        params: dict[str, str],
        query: str,
    ) -> dict | None:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 429:
                wait = min(int(response.headers.get("Retry-After", "10")), 60)
                log_event("rapidapi_rate_limit", source=self.name, query=query, wait=wait)
                time.sleep(wait)
                response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get("hasError"):
                log_event("jobsapi_bing_error", query=query, errors=data.get("errors"))
                return None
            if "data" not in data:
                # Unrecognized envelope (e.g. {"message": "...rate limit..."}
                # observed on transient PRO-tier bursts). Don't silently treat
                # as success — the #601 bug class is exactly "unexpected shape
                # passes through as an empty row". Surface it loudly in the
                # log so the next variant is visible.
                log_event(
                    "jobsapi_bing_unrecognized_response",
                    query=query,
                    status=response.status_code,
                    body_excerpt=str(data)[:300],
                )
                return None
            return data
        except requests.RequestException as e:
            log_event("jobsapi_bing_error", query=query, error=str(e))
            return None

    def _parse_search_rows(self, data: dict) -> list[dict]:
        # Extract minimal hints from /v2/bing/search: id (required for the
        # get-call), title, location. company is intentionally ignored here
        # — the search response's `company` key is unreliable and the
        # canonical `companyName` only appears in /v2/bing/get.
        hints: list[dict] = []
        for job in data.get("data", []) or []:
            job_id = str(job.get("id", ""))
            if not job_id:
                continue
            title = clean_title(job.get("title", ""))
            if not title:
                continue
            loc = job.get("location", "")
            location = loc.get("location", "") if isinstance(loc, dict) else loc
            hints.append({"id": job_id, "title": title, "location": location})
        return hints

    def _fetch_detail(self, headers: dict[str, str], job_id: str, query: str) -> dict | None:
        return self._call_with_retry(self._GET_ENDPOINT, headers, {"id": job_id}, query)

    def _compose_row(self, hint: dict, detail: dict, query: str) -> dict | None:
        # /v2/bing/get wraps the actual record under a top-level `data` key
        # (confirmed via #765 follow-up live capture against the operator's
        # stack 2026-05-23). The first-pass fix shipped under #765 read the
        # fields off `detail` directly, matching the synthetic test fixture
        # shape but not the real API shape — every row dropped at intake.
        # `data` is the unwrap point; `companyName` (NOT `company`, which
        # only appears on /v2/bing/search) is the canonical key.
        record = detail.get("data") or {}
        if not isinstance(record, dict):
            return None
        url = record.get("applyUrl", "")
        if not url:
            return None
        company = clean_company(record.get("companyName", ""))
        return {
            "title": hint["title"],
            "company": company,
            "location": hint["location"],
            "url": url,
            "api_id": hint["id"],
            "source": self.source_label,
            "query": query,
            "description": record.get("description", ""),
        }
