"""AlgoraBountiesAdapter — Algora public bounty API (#853 Phase 3).

Algora surfaces open-source bounties at `console.algora.io/api/orgs/{org}/bounties`.
Public endpoint, no auth required. Operator enumerates target orgs via
`config/algora_orgs.txt` (one org slug per line). Attribution: link back to the
Algora bounty page per their public API usage norms.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import requests

from findajob.audit import log_event
from findajob.cleaning import clean_company, clean_title
from findajob.paths import BASE

from .base import LiveTestResult, QueryResult


class AlgoraBountiesAdapter:
    """Algora bounty ingestion via public per-org JSON API.

    Each org has its own endpoint. The adapter iterates over org slugs
    from `config/algora_orgs.txt` and fetches bounties for each.
    """

    name: ClassVar[str] = "algora-bounties"
    display_name: ClassVar[str] = "Algora Bounties"
    source_label: ClassVar[str] = "algora_bounties"
    required_env_vars: ClassVar[tuple[str, ...]] = ()

    _BASE_URL: ClassVar[str] = "https://console.algora.io/api/orgs"
    _UA: ClassVar[str] = "findajob-pipeline/1.0 (personal job search tool)"

    def _config_path(self) -> Path:
        return Path(BASE) / "config" / "algora_orgs.txt"

    def _read_orgs(self) -> list[str]:
        path = self._config_path()
        if not path.exists():
            return []
        orgs: list[str] = []
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            orgs.append(line)
        return orgs

    def is_configured(self) -> bool:
        return len(self._read_orgs()) > 0

    def fetch(self, queries: list[str]) -> list[dict]:
        del queries
        orgs = self._read_orgs()
        if not orgs:
            return []
        jobs: list[dict] = []
        for org in orgs:
            url = f"{self._BASE_URL}/{org}/bounties"
            try:
                resp = requests.get(url, headers={"User-Agent": self._UA}, timeout=15)
            except Exception as e:
                log_event("algora_fetch_error", org=org, error=str(e))
                continue
            if resp.status_code != 200:
                log_event("algora_fetch_skip", org=org, status=resp.status_code)
                continue
            try:
                payload = resp.json()
            except ValueError as e:
                log_event("algora_fetch_invalid_json", org=org, error=str(e))
                continue
            bounties = payload if isinstance(payload, list) else payload.get("bounties", [])
            if not isinstance(bounties, list):
                log_event("algora_fetch_invalid_shape", org=org, got=type(bounties).__name__)
                continue
            for b in bounties:
                if not isinstance(b, dict):
                    continue
                title = b.get("title", "") or ""
                reward = b.get("reward_formatted", "") or b.get("reward", "")
                if reward:
                    title = f"{title} [{reward}]"
                jobs.append(
                    {
                        "title": clean_title(title),
                        "company": clean_company(org.replace("-", " ").title()),
                        "url": b.get("url", "") or f"https://console.algora.io/org/{org}/bounties",
                        "location": "Remote",
                        "source": self.source_label,
                        "description": b.get("description", "") or b.get("body", "") or "",
                    }
                )
        log_event("algora_fetch", count=len(jobs), orgs=len(orgs))
        return jobs

    def live_test(self, queries: list[str]) -> LiveTestResult:
        del queries
        orgs = self._read_orgs()
        if not orgs:
            return LiveTestResult(
                ok=False,
                bucket="auth",
                per_query=[],
                auth_error="No orgs configured in config/algora_orgs.txt.",
            )
        org = orgs[0]
        url = f"{self._BASE_URL}/{org}/bounties"
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
                ok=False, bucket="server", per_query=[], auth_error=f"HTTP {resp.status_code} for org '{org}'."
            )
        try:
            payload = resp.json()
        except ValueError:
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Invalid JSON response.")
        bounties = payload if isinstance(payload, list) else payload.get("bounties", [])
        if not isinstance(bounties, list):
            return LiveTestResult(ok=False, bucket="server", per_query=[], auth_error="Unexpected response shape.")
        count = len(bounties)
        per_query = [QueryResult(query=org, count=count)]
        if count > 0:
            return LiveTestResult(ok=True, bucket="success", per_query=per_query, auth_error=None)
        return LiveTestResult(ok=True, bucket="zero_rows", per_query=per_query, auth_error=None)
