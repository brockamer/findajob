"""JobSourceAdapter Protocol + result dataclasses (#408).

Every job-source adapter implements this Protocol. The framework is
source-agnostic by design — RapidAPI-flavored adapters (jobs-api14,
jsearch) ship in #408; future direct adapters (Workday CXS #248,
Gem GraphQL #249) implement the same contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

LiveTestBucket = Literal[
    "success",  # all queries returned 200 + ≥1 row
    "mixed",  # some queries returned rows, some empty (no errors)
    "zero_rows",  # all queries returned 200 but 0 rows
    "auth",  # call 1 hit 401/403 — bad key or inactive subscription
    "rate_limit",  # call 1 succeeded, later call hit 429
    "server",  # 5xx response from the API
    "network",  # DNS/TCP/TLS failure or timeout
]


@dataclass(frozen=True)
class QueryResult:
    """Per-query result from a live test.

    `count` is the number of jobs returned for `query`. `error` is None
    on success, or a short human-language string on per-query failure
    (rare — usually the whole live test fails together).
    """

    query: str
    count: int
    error: str | None = None


@dataclass(frozen=True)
class LiveTestResult:
    """Structured result of `JobSourceAdapter.live_test()`.

    The form renders different cards based on `bucket`. `ok` is True
    if the connection works (success / mixed / zero_rows); False if a
    failure prevented completion (auth / rate_limit / server / network).
    """

    ok: bool
    bucket: LiveTestBucket
    per_query: list[QueryResult] = field(default_factory=list)
    auth_error: str | None = None


@runtime_checkable
class JobSourceAdapter(Protocol):
    """Source-agnostic adapter contract.

    Adapters declare their identity (name, display_name, source_label)
    and required env vars as class attributes. Three methods drive
    runtime behavior: is_configured (env var presence check), fetch
    (production call, returns raw row dicts), live_test (onboarding-time
    connection check, returns structured LiveTestResult).
    """

    name: str
    display_name: str
    source_label: str
    required_env_vars: tuple[str, ...]

    def is_configured(self) -> bool: ...
    def fetch(self, queries: list[str]) -> list[dict]: ...
    def live_test(self, queries: list[str]) -> LiveTestResult: ...
