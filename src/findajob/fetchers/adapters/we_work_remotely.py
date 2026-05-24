"""WeWorkRemotelyAdapter — public WWR RSS feed (#853 Phase 2).

Endpoint: `https://weworkremotely.com/remote-jobs.rss`. No auth. RSS-XML
format (parsed via stdlib xml.etree.ElementTree). ~99 items per fetch
in the default all-categories feed.

WWR title format is `"Company Name: Job Title"` — the adapter splits on
the first `: ` to separate company from title. Listings without that
delimiter fall back to "WWR" as company.

Linkback to WWR's listing page is automatic via the canonical `url`
mapping (RSS `<link>`); WWR doesn't publish explicit API ToS at the
feed URL, but the same attribution-via-linkback discipline applies.
"""

from __future__ import annotations

import html
import time
import xml.etree.ElementTree as ET
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title

from .base import LiveTestResult, QueryResult


class WeWorkRemotelyAdapter:
    """We Work Remotely board-feed ingestion via public RSS feed.

    Single endpoint at `weworkremotely.com/remote-jobs.rss`. No auth, no
    per-company enumeration. `queries` parameter is ignored.
    """

    name: ClassVar[str] = "we-work-remotely"
    display_name: ClassVar[str] = "We Work Remotely"
    source_label: ClassVar[str] = "wwr_rss"
    required_env_vars: ClassVar[tuple[str, ...]] = ()

    _ENDPOINT: ClassVar[str] = "https://weworkremotely.com/remote-jobs.rss"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"

    def is_configured(self) -> bool:
        return True

    def _parse_items(self, xml_text: str) -> list[dict]:
        """Parse the RSS XML and yield normalized job-row dicts."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            log_event("we_work_remotely_parse_error", error=str(e))
            return []
        channel = root.find("channel")
        if channel is None:
            return []
        rows: list[dict] = []
        for item in channel.findall("item"):
            title_raw = (item.findtext("title") or "").strip()
            # WWR title shape: "Company Name: Job Title" — split on first `: `.
            if ": " in title_raw:
                company_part, title_part = title_raw.split(": ", 1)
            else:
                company_part, title_part = "WWR", title_raw
            link = (item.findtext("link") or item.findtext("guid") or "").strip()
            description = (item.findtext("description") or "").strip()
            # Location: prefer region, fall back to state/country.
            region = (item.findtext("region") or "").strip()
            country = (item.findtext("country") or "").strip()
            state = (item.findtext("state") or "").strip()
            location_parts = [p for p in (region, state, country) if p]
            location = ", ".join(location_parts)
            rows.append(
                {
                    "title": clean_title(title_part),
                    "company": clean_company(company_part),
                    "url": link,
                    "location": location,
                    "source": self.source_label,
                    "description": html.unescape(description),
                }
            )
        return rows

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        headers = {"User-Agent": self._UA}
        try:
            resp = requests.get(self._ENDPOINT, headers=headers, timeout=15)
        except Exception as e:
            log_event("we_work_remotely_fetch_error", error=str(e))
            return []
        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", "10")), 60)
            log_event("we_work_remotely_rate_limit", wait=wait)
            time.sleep(wait)
            try:
                resp = requests.get(self._ENDPOINT, headers=headers, timeout=15)
            except Exception as e:
                log_event("we_work_remotely_fetch_error", error=str(e))
                return []
        if resp.status_code != 200:
            log_event("we_work_remotely_fetch_skip", status=resp.status_code)
            return []
        rows = self._parse_items(resp.text)
        log_event("we_work_remotely_fetch", count=len(rows))
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
        rows = self._parse_items(resp.text)
        per_query = [QueryResult(query="all", count=len(rows))]
        if len(rows) > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
