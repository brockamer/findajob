"""Live OpenRouter credit-remaining lookup for the nav chip (#665).

Fetches ``GET /api/v1/auth/key`` and surfaces ``limit_remaining`` (USD) so
the nav bar can answer "when will I run out of credit?". Cached in-process
with a short TTL to avoid hammering OpenRouter on every page render.

Lives outside ``findajob.cost_rollups`` because that module's docstring
constrains it to "pure functions over a sqlite3.Connection, no HTTP, no
env reads, no side effects beyond SELECTs". This module does HTTP + env.

**Failure-open contract.** Any of the following return ``None``; callers
(specifically the nav template) hide the chip silently — never raise,
never 500:

- ``OPENROUTER_API_KEY`` env var missing or blank.
- HTTP 4xx / 5xx / timeout / DNS failure / TLS error.
- Non-JSON response body.
- Missing ``data.limit_remaining`` (free-tier or no-limit keys).
- Any unexpected schema shape.

**Use the dedicated ``limit_remaining`` field, not ``limit - usage``.**
The issue body's formula assumed a single lifetime cap, but OpenRouter
supports periodic resets (weekly/monthly): in that case ``usage`` is
lifetime cumulative while ``limit`` is per-period, so subtracting them
gives a meaningless negative number. ``limit_remaining`` is what the API
itself computes per-period — exactly what the operator wants to see.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

_OPENROUTER_AUTH_KEY_URL = "https://openrouter.ai/api/v1/auth/key"
_CACHE_TTL_S = 300  # 5 minutes — within the 5–10 min AC band
_HTTP_TIMEOUT_S = 5.0

CreditState = Literal["normal", "amber", "red"]


@dataclass(frozen=True)
class CreditInfo:
    remaining_usd: float
    state: CreditState


# Single-process in-memory cache: (expires_at_monotonic, value).
# Restart resets. findajob runs single-worker uvicorn so per-worker cache
# is the whole cache; multi-worker would mean each worker fetches once
# per TTL — still acceptable load on OpenRouter.
_cache: tuple[float, CreditInfo | None] | None = None


def _threshold(env_var: str, default: float) -> float:
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _classify(remaining: float) -> CreditState:
    red = _threshold("OPENROUTER_CREDIT_RED_USD", 1.0)
    amber = _threshold("OPENROUTER_CREDIT_AMBER_USD", 5.0)
    if remaining < red:
        return "red"
    if remaining < amber:
        return "amber"
    return "normal"


def _fetch() -> CreditInfo | None:
    """One live call to OpenRouter. Returns None on every failure path."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None
    req = urllib.request.Request(  # noqa: S310 — fixed https URL
        _OPENROUTER_AUTH_KEY_URL,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310
            payload = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        body = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    remaining = data.get("limit_remaining")
    if remaining is None:
        return None
    try:
        remaining_f = float(remaining)
    except (TypeError, ValueError):
        return None
    return CreditInfo(remaining_usd=remaining_f, state=_classify(remaining_f))


def credit_remaining() -> CreditInfo | None:
    """Cached lookup of OpenRouter remaining credit.

    Returns ``None`` on any failure mode — callers must treat ``None`` as
    "hide the chip", not as an error condition. ``None`` results are
    cached too so a bad/missing key doesn't trigger a retry storm.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None:
        expires_at, value = _cache
        if now < expires_at:
            return value
    value = _fetch()
    _cache = (now + _CACHE_TTL_S, value)
    return value


def reset_cache_for_tests() -> None:
    """Clear the in-process cache. Test-only — not for production code."""
    global _cache
    _cache = None
