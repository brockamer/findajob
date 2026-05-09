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
from .jobs_api14_indeed import JobsApi14IndeedAdapter
from .jsearch import JSearchAdapter
from .lever import LeverAdapter

REGISTERED_ADAPTERS: list[type[JobSourceAdapter]] = [
    JobsApi14Adapter,  # type: ignore[list-item]
    JobsApi14IndeedAdapter,  # type: ignore[list-item]
    JSearchAdapter,  # type: ignore[list-item]
    GreenhouseAdapter,  # type: ignore[list-item]
    AshbyAdapter,  # type: ignore[list-item]
    LeverAdapter,  # type: ignore[list-item]
    GmailLinkedInAdapter,  # type: ignore[list-item]
]

_DEFAULT_ACTIVE_SOURCES: list[str] = ["jobs-api14"]


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
