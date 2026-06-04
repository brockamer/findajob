"""Shared feed-URL probe helper (#984).

Probes each ATS board URL in ``feed_urls.txt`` against its public API for
liveness. One source of truth reused by three surfaces:

* onboarding-time validation (#984) — prevention, before first triage
* the "Verify feed URLs" settings button (#985) — self-serve remediation
* the runtime persistent-404 health-check (#983) — detection over time

Design note — *no drift*: the per-ATS slug regex and endpoint template are
read directly off the adapter classes (``GreenhouseAdapter._SLUG_RE`` etc.)
rather than copied here. If an adapter's URL convention changes, the probe
follows automatically. Reaching for those underscore-prefixed ClassVars is
deliberate within-package reuse, not a layering violation.

Contract: probing NEVER raises into the caller. A network failure, timeout,
or malformed response becomes a result with ``status="unreachable"`` — so
onboarding completion can never be blocked by a slow or offline ATS API.
"""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

import requests

from .adapters.ashby import AshbyAdapter
from .adapters.greenhouse import GreenhouseAdapter
from .adapters.lever import LeverAdapter

ProbeStatus = Literal["live", "dead", "unreachable", "unsupported"]

# (kind, slug_regex, endpoint_template) sourced from the adapters themselves
# so there is exactly one definition of each ATS's URL shape.
_PROBE_KINDS = (
    ("greenhouse", GreenhouseAdapter._SLUG_RE, GreenhouseAdapter._ENDPOINT_TEMPLATE),
    ("ashby", AshbyAdapter._SLUG_RE, AshbyAdapter._ENDPOINT_TEMPLATE),
    ("lever", LeverAdapter._SLUG_RE, LeverAdapter._ENDPOINT_TEMPLATE),
)

_DEFAULT_TIMEOUT = 8.0
_DEFAULT_MAX_WORKERS = 8

# Match the adapters' User-Agent exactly — no-drift extends to headers, not just
# the URL. An ATS that filters the bare python-requests UA would block the probe
# (false "unreachable") while the real fetch, which sends this, succeeds.
_UA = "findajob-pipeline/1.0 (personal job search tool)"

# A clean company display name is short and word-like. Beyond these bounds it
# reads as provenance/free-text — the #856 failure mode where junk comments
# pollute jobs.company and diverge the dedup fingerprint.
_MAX_COMPANY_NAME_LEN = 50
_MAX_COMPANY_NAME_WORDS = 6


@dataclass(frozen=True)
class FeedProbeResult:
    """Outcome of probing one ``feed_urls.txt`` line."""

    line: str
    kind: str | None
    slug: str | None
    status: ProbeStatus
    http_status: int | None
    reason: str
    company: str | None
    company_name_ok: bool


def is_plausible_company_name(name: str | None) -> bool:
    """True unless ``name`` looks like junk (a URL or sentence-like provenance).

    Flags the #856 failure mode only — *present-but-polluting* comments. A
    missing comment (None/empty) is not junk: the Ashby/Lever parser falls
    back to ``slug.title()``, so there is nothing to object to → True.
    """
    if name is None:
        return True
    text = name.strip()
    if not text:
        return True
    lowered = text.lower()
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        return False
    if len(text) > _MAX_COMPANY_NAME_LEN:
        return False
    if len(text.split()) > _MAX_COMPANY_NAME_WORDS:
        return False
    return True


def _classify(slug: str, status_code: int) -> tuple[ProbeStatus, str]:
    """Map an HTTP status to a probe verdict + plain-language reason.

    A 404 condemns the slug (dead). Any other non-200 — 5xx, 429, redirects —
    is treated as transient (unreachable), never as a dead slug, so a server
    blip can't make the UI cry wolf on a healthy feed.
    """
    if status_code == 200:
        return "live", "Live — board responded 200."
    if status_code == 404:
        return (
            "dead",
            f"404 — slug '{slug}' isn't a valid board; the company may have changed "
            "or left this ATS. Correct the slug, or comment the line out.",
        )
    return (
        "unreachable",
        f"HTTP {status_code} — couldn't verify right now (looks transient); try again later.",
    )


def probe_feed_line(line: str, *, timeout: float = _DEFAULT_TIMEOUT) -> FeedProbeResult | None:
    """Probe one feed_urls.txt line. Returns None for nothing-to-probe lines.

    Never raises on probe I/O: a network failure, timeout, or HTTP error becomes
    ``status="unreachable"`` (or ``"dead"`` for a 404). A non-empty URL on no
    supported ATS is flagged ``status="unsupported"`` — surfaced, never dropped.

    Caller misuse is *not* masked: an invalid ``timeout`` argument surfaces its
    ValueError rather than being swallowed into a false "unreachable" (which
    would make every feed look dead and bury the bug). The batch wrapper
    ``probe_feed_urls`` absorbs even that, so onboarding can never be blocked.
    """
    text = line.strip()
    url_part, _, comment = text.partition("#")
    url_part = url_part.strip()
    if not url_part:
        return None  # blank line or comment-only heading — nothing to probe
    company = comment.strip() or None
    company_ok = is_plausible_company_name(company)

    for kind, slug_re, template in _PROBE_KINDS:
        m = slug_re.search(url_part)
        if not m:
            continue
        slug = m.group(1)
        api_url = template.format(slug=slug)
        try:
            resp = requests.get(api_url, headers={"User-Agent": _UA}, timeout=timeout)
        except requests.RequestException:
            return FeedProbeResult(
                line=text,
                kind=kind,
                slug=slug,
                status="unreachable",
                http_status=None,
                reason="Couldn't reach the ATS API (network error or timeout); try again later.",
                company=company,
                company_name_ok=company_ok,
            )
        status, reason = _classify(slug, resp.status_code)
        return FeedProbeResult(
            line=text,
            kind=kind,
            slug=slug,
            status=status,
            http_status=resp.status_code,
            reason=reason,
            company=company,
            company_name_ok=company_ok,
        )

    return FeedProbeResult(
        line=text,
        kind=None,
        slug=None,
        status="unsupported",
        http_status=None,
        reason=(
            "Unsupported ATS — findajob probes Greenhouse, Ashby, and Lever boards. "
            "Comment this line out, or check whether the company is on a supported ATS."
        ),
        company=company,
        company_name_ok=company_ok,
    )


def _safe_probe(line: str, timeout: float) -> FeedProbeResult | None:
    """probe_feed_line wrapped so NOTHING escapes — the ultimate fail-safe.

    probe_feed_line already swallows request errors; this guards against any
    other unexpected exception so a single bad line can never abort the batch
    (and thus never block onboarding).
    """
    try:
        return probe_feed_line(line, timeout=timeout)
    except Exception:  # noqa: BLE001 — deliberate fail-safe; see never-block-onboarding contract
        return FeedProbeResult(
            line=line.strip(),
            kind=None,
            slug=None,
            status="unreachable",
            http_status=None,
            reason="Couldn't verify this line (unexpected error); skipped.",
            company=None,
            company_name_ok=True,
        )


def probe_feed_urls(
    lines: Iterable[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> list[FeedProbeResult]:
    """Probe every line concurrently (bounded), preserving input order.

    Nothing-to-probe lines (blank / comment-only) are dropped; everything
    else — including unsupported and unreachable lines — is returned, so the
    caller can surface a flag rather than silently lose a line. Never raises.
    """
    items = list(lines)
    if not items:
        return []
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(lambda ln: _safe_probe(ln, timeout), items)
    return [r for r in results if r is not None]
