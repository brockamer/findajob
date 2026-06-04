"""#985: probe configured ATS feeds (Greenhouse/Lever/Ashby) for live/dead.

Shared by /settings/feed-urls/ (the Verify button) and #984 (onboarding slug
validation). Parses feed_urls.txt rows and probes each against its ATS API,
classifying by HTTP status only — liveness needs the status code, not the
body, which sidesteps the per-ATS response-shape differences.

Coexists deliberately with each adapter's `live_test()`: that method is
adapter-scoped + query-based and buckets 404 as 'auth' (wrong granularity and
vocabulary for per-row display, wrong shape for #984's per-slug need). Do NOT
consolidate the two or refactor live_test to delegate.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Literal

import requests

from findajob.fetchers.adapters.ashby import AshbyAdapter
from findajob.fetchers.adapters.greenhouse import GreenhouseAdapter
from findajob.fetchers.adapters.lever import LeverAdapter


@dataclass(frozen=True)
class _ProbeSpec:
    ats: str
    slug_re: re.Pattern
    endpoint: str
    comment_is_company: bool


# Single source of ATS metadata: read straight from the adapter ClassVars so
# the regex/endpoint can never drift from what triage actually fetches.
_REGISTRY: tuple[_ProbeSpec, ...] = (
    _ProbeSpec(
        "greenhouse", GreenhouseAdapter._SLUG_RE, GreenhouseAdapter._ENDPOINT_TEMPLATE, comment_is_company=False
    ),
    _ProbeSpec("lever", LeverAdapter._SLUG_RE, LeverAdapter._ENDPOINT_TEMPLATE, comment_is_company=True),
    _ProbeSpec("ashby", AshbyAdapter._SLUG_RE, AshbyAdapter._ENDPOINT_TEMPLATE, comment_is_company=True),
)
_BY_ATS: dict[str, _ProbeSpec] = {s.ats: s for s in _REGISTRY}

DEFAULT_TIMEOUT: float = 8.0
_UA: str = GreenhouseAdapter._UA  # reuse the adapter UA; do not re-declare

ProbeStatus = Literal["live", "dead", "unreachable", "unsupported"]

_UNSUPPORTED_HINT = (
    "Not a Greenhouse, Lever, or Ashby URL — findajob can't fetch this ATS. "
    "Comment it out with # in /config/ → feed_urls.txt."
)
_OFFLINE_HINT = (
    "Couldn't reach this feed (timed out or no network). If every feed is unreachable, this instance is likely offline."
)
_DEAD_404_HINT = (
    "404 — this slug may have changed. Check the company's careers page for the current URL, or comment it out with #."
)


@dataclass(frozen=True)
class FeedRow:
    """One feed_urls.txt line, parsed but not yet probed."""

    url: str  # the URL part, comment stripped
    ats: str | None  # None => unsupported ATS
    slug: str | None
    company: str  # inline comment (Lever/Ashby) | slug.title() | url (unsupported)


def parse_feed_rows(text: str) -> list[FeedRow]:
    """Parse feed_urls.txt content into display/probe rows.

    Skips blank lines and lines starting with '#' (intentional comment-outs).
    Classifies each remaining line by ATS via the adapter regexes; non-matching
    lines become `ats=None` (unsupported). Company name comes from the inline
    '# comment' for Lever/Ashby only, else the titlecased slug. De-dupes by
    (ats, slug), first occurrence wins.
    """
    # NB: parallels adapters/_slugs.py::_parse_feed_slugs (the per-adapter parser).
    # This is the multi-ATS superset used by the verify page + #984; keep the two
    # in sync if feed_urls.txt syntax changes.
    rows: list[FeedRow] = []
    seen: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url_part, _, comment = line.partition("#")
        url_part = url_part.strip()
        comment = comment.strip()

        matched: tuple[_ProbeSpec, re.Match] | None = None
        for spec in _REGISTRY:
            m = spec.slug_re.search(url_part)
            if m:
                matched = (spec, m)
                break

        if matched is None:
            rows.append(FeedRow(url=url_part, ats=None, slug=None, company=url_part))
            continue

        spec, m = matched
        slug = m.group(1)
        key = (spec.ats, slug)
        if key in seen:
            continue
        seen.add(key)
        company = comment if (comment and spec.comment_is_company) else slug.title()
        rows.append(FeedRow(url=url_part, ats=spec.ats, slug=slug, company=company))
    return rows


@dataclass(frozen=True)
class ProbeResult:
    row: FeedRow
    status: ProbeStatus
    http_code: int | None  # set for live/dead; None for unreachable/unsupported
    hint: str  # "" for live; plain-language guidance otherwise


def probe_feed_url(row: FeedRow, timeout: float = DEFAULT_TIMEOUT) -> ProbeResult:
    """Probe one feed for liveness. Classifies by HTTP status only.

    `stream=True` is load-bearing: Greenhouse's endpoint ends in `?content=true`,
    so a plain GET would download every job's HTML on a liveness check. With
    stream=True only the status line + headers are fetched; we read status_code
    and close without ever pulling the body.
    """
    if row.ats is None or row.slug is None:
        return ProbeResult(row=row, status="unsupported", http_code=None, hint=_UNSUPPORTED_HINT)

    spec = _BY_ATS[row.ats]
    url = spec.endpoint.format(slug=row.slug)
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout, stream=True)
    except requests.RequestException:
        return ProbeResult(row=row, status="unreachable", http_code=None, hint=_OFFLINE_HINT)
    try:
        code = resp.status_code
    finally:
        resp.close()

    if code == 200:
        return ProbeResult(row=row, status="live", http_code=200, hint="")
    if code == 404:
        return ProbeResult(row=row, status="dead", http_code=404, hint=_DEAD_404_HINT)
    return ProbeResult(
        row=row,
        status="dead",
        http_code=code,
        hint=(
            f"Unexpected response (HTTP {code}) — the feed may be misconfigured "
            "or the ATS is having trouble; re-verify shortly."
        ),
    )


def probe_all(rows: list[FeedRow], timeout: float = DEFAULT_TIMEOUT, max_workers: int = 8) -> list[ProbeResult]:
    """Probe every row concurrently, preserving input order. One row raising
    never breaks the batch — it degrades to a single 'unreachable' result."""
    if not rows:
        return []
    workers = max(1, min(len(rows), max_workers))
    results: list[ProbeResult | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(probe_feed_url, row, timeout): i for i, row in enumerate(rows)}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = ProbeResult(row=rows[i], status="unreachable", http_code=None, hint=_OFFLINE_HINT)
    return [r for r in results if r is not None]
