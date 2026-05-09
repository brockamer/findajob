"""Adapter registry + active-source resolution (#408)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from findajob.audit import log_event
from findajob.paths import BASE

from .ashby import AshbyAdapter
from .base import JobSourceAdapter
from .gmail import GmailLinkedInAdapter
from .greenhouse import GreenhouseAdapter
from .jobs_api14 import JobsApi14Adapter
from .jobs_api14_bing import JobsApi14BingAdapter
from .jobs_api14_indeed import JobsApi14IndeedAdapter
from .jsearch import JSearchAdapter
from .lever import LeverAdapter

REGISTERED_ADAPTERS: list[type[JobSourceAdapter]] = [
    JobsApi14Adapter,  # type: ignore[list-item]
    JobsApi14IndeedAdapter,  # type: ignore[list-item]
    JobsApi14BingAdapter,  # type: ignore[list-item]
    JSearchAdapter,  # type: ignore[list-item]
    GreenhouseAdapter,  # type: ignore[list-item]
    AshbyAdapter,  # type: ignore[list-item]
    LeverAdapter,  # type: ignore[list-item]
    GmailLinkedInAdapter,  # type: ignore[list-item]
]

# Default when config/active_sources.txt is missing or empty: every adapter
# whose pre-#410.5 behavior was "fired by the orchestrator regardless of
# active_sources.txt." Pre-#410.5 the orchestrator fired the four
# fetch_*_jobs wrappers (greenhouse / ashby / lever / gmail) unconditionally
# and only the RapidAPI adapters (jobs-api14 / jobs-api14-indeed / jsearch)
# were registry-gated. Keeping the pre-#408 ["jobs-api14"]-only default
# after #410.5 would silently drop four sources for any stack without an
# explicit file. is_configured() remains the correct gate for "can this
# adapter run on this stack" — active_sources.txt is for "operator opted
# some out", not "operator forgot to opt in".
#
# **New adapters added post-#410.5 are NOT auto-enabled.** They go into
# REGISTERED_ADAPTERS (so the conformance + registry-membership invariants
# see them) but stay out of this default list — operators opt in via
# `config/active_sources.txt`. `jobs-api14-bing` (#422) is the first
# adapter to follow this opt-in pattern.
_DEFAULT_ACTIVE_SOURCES: list[str] = [
    "jobs-api14",
    "jobs-api14-indeed",
    "jsearch",
    "greenhouse",
    "ashby",
    "lever",
    "gmail",
    # jobs-api14-bing intentionally omitted — opt-in only (#422 AC #3).
]


def _active_sources_path() -> Path:
    return Path(BASE) / "config" / "active_sources.txt"


def _read_active_sources(path: Path | None = None) -> list[str]:
    """Return the list of adapter names active for this stack.

    Backwards-compat: if the file is missing or empty, returns ['jobs-api14'].
    """
    target = path or _active_sources_path()
    if not target.exists():
        return list(_DEFAULT_ACTIVE_SOURCES)
    names: list[str] = []
    for raw in target.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line)
    return names if names else list(_DEFAULT_ACTIVE_SOURCES)


def iter_configured_adapters() -> Iterator[JobSourceAdapter]:
    """Yield adapter instances active for this stack and properly configured."""
    active_names = _read_active_sources()
    for cls in REGISTERED_ADAPTERS:
        if cls.name not in active_names:
            continue
        instance = cls()
        if not instance.is_configured():
            log_event("adapter_not_configured", adapter=cls.name)
            continue
        yield instance


def _write_active_sources(names: list[str], path: Path | None = None) -> None:
    """Atomically write the active-sources list to ``config/active_sources.txt``.

    Header comment + one adapter name per line. Atomic via tmp + os.replace
    so a crash mid-write doesn't truncate the file (matches the
    `gmail_imap.save_config` pattern). Empty `names` produces a header-only
    file — `_read_active_sources` treats that as empty and falls back to
    `_DEFAULT_ACTIVE_SOURCES`, which is the intentional behavior for the
    settings UI's "uncheck everything → revert to default" flow.
    """
    import os

    target = path or _active_sources_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    body = "# Managed by /settings/active-sources/. Edit there to keep this file in sync.\n"
    for name in names:
        body += f"{name}\n"
    tmp.write_text(body)
    os.replace(str(tmp), str(target))
